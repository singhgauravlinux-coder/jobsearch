import { withBrowser, extractJobPostingJsonLd, htmlToText, writeError } from "../helpers.js"

export interface DetailOpts {
  id: string // a job URL (preferred) or a bare numeric jobId
  format: "json" | "plain"
}

function toUrl(id: string): string {
  if (id.startsWith("http")) return id
  return `https://www.naukri.com/job-listings-${id}`
}

export async function runDetail(opts: DetailOpts): Promise<number> {
  try {
    const url = toUrl(opts.id)

    const result = await withBrowser(async (page) => {
      await page.goto(url, { waitUntil: "networkidle", timeout: 25000 }).catch(() => null)
      const html = await page.content()

      const jsonLd = extractJobPostingJsonLd(html)
      if (jsonLd && typeof jsonLd.description === "string") {
        return {
          id: opts.id,
          title: typeof jsonLd.title === "string" ? jsonLd.title : null,
          company:
            jsonLd.hiringOrganization && typeof jsonLd.hiringOrganization === "object"
              ? (jsonLd.hiringOrganization as any).name ?? null
              : null,
          description: htmlToText(jsonLd.description),
          url,
          source: "json-ld" as const,
        }
      }

      // Fallback: schema.org markup wasn't present or had no description -
      // take the largest text block on the rendered page. Naukri's own CSS
      // module class names are hashed per-build and not worth hardcoding;
      // the job description is reliably the longest contiguous text node on
      // a job detail page, so this heuristic degrades gracefully instead of
      // breaking outright on a frontend redeploy.
      const longestText = await page.evaluate(() => {
        const blocks = Array.from(document.querySelectorAll("div, section, article"))
          .map((el) => (el as HTMLElement).innerText || "")
          .filter((t) => t.length > 200)
        return blocks.sort((a, b) => b.length - a.length)[0] ?? null
      })

      const titleText = await page.title()
      return {
        id: opts.id,
        title: titleText || null,
        company: null,
        description: longestText ? longestText.trim() : null,
        url,
        source: "heuristic" as const,
      }
    })

    if (!result.description) {
      writeError(
        "Couldn't extract a description from this page - it may have been removed, or Naukri's page structure changed.",
        "NO_DESCRIPTION",
      )
      return 1
    }

    if (opts.format === "plain") {
      process.stdout.write(result.description + "\n")
    } else {
      process.stdout.write(JSON.stringify(result) + "\n")
    }
    return 0
  } catch (e) {
    writeError(e instanceof Error ? e.message : String(e), "DETAIL_FAILED")
    return 1
  }
}
