import { withBrowser, buildSearchUrl, normalizeJob, writeError, type NormalizedJob } from "../helpers.js"

export interface SearchOpts {
  query?: string
  location?: string
  experienceMin?: number
  jobage?: number // days; filtered client-side, see module comment in helpers.ts
  page: number
  limit?: number
  format: "json" | "table" | "plain"
}

const RESULTS_PER_PAGE = 20

async function fetchPage(query: string | undefined, location: string | undefined, pageNum: number): Promise<NormalizedJob[]> {
  return withBrowser(async (page) => {
    const url = buildSearchUrl(query, location, pageNum)

    const responsePromise = page
      .waitForResponse(
        (r) => r.url().includes("/jobapi/") && r.url().includes("search") && r.status() === 200,
        { timeout: 25000 },
      )
      .catch(() => null)

    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 25000 }).catch(() => null)
    const response = await responsePromise
    if (!response) return []

    let data: unknown
    try {
      data = await response.json()
    } catch {
      return []
    }

    const jobDetails = data && typeof data === "object" ? (data as any).jobDetails : null
    if (!Array.isArray(jobDetails)) return []

    return jobDetails.map(normalizeJob).filter((j): j is NormalizedJob => j !== null)
  })
}

export async function runSearch(opts: SearchOpts): Promise<number> {
  try {
    const limit = opts.limit ?? 20
    const results: NormalizedJob[] = []
    let pageNum = opts.page
    const maxPages = Math.max(1, Math.ceil(limit / RESULTS_PER_PAGE)) + 1 // +1: room for client-side filtering to drop some

    for (let i = 0; i < maxPages && results.length < limit; i++) {
      const jobs = await fetchPage(opts.query, opts.location, pageNum)
      if (jobs.length === 0) break // no more results, or the page's own API call never fired

      for (const job of jobs) {
        if (opts.experienceMin !== undefined && job.experienceMaxYears !== null && job.experienceMaxYears < opts.experienceMin) {
          continue
        }
        if (opts.jobage !== undefined && job.postedDaysAgo !== null && job.postedDaysAgo > opts.jobage) {
          continue
        }
        results.push(job)
        if (results.length >= limit) break
      }
      pageNum++
    }

    const trimmed = results.slice(0, limit)

    if (opts.format === "json") {
      process.stdout.write(JSON.stringify(trimmed) + "\n")
    } else {
      for (const j of trimmed) {
        const line = `${j.title} — ${j.company ?? "Unknown"} (${j.location ?? "—"}) [${j.experienceText ?? "—"}] ${j.url}`
        process.stdout.write(line + "\n")
      }
    }
    return 0
  } catch (e) {
    writeError(e instanceof Error ? e.message : String(e), "SEARCH_FAILED")
    return 1
  }
}
