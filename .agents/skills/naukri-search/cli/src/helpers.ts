// Data source: Naukri.com's own frontend. Naukri's /jobapi/v3/search endpoint
// now requires a signed `nkparam` header generated inside their obfuscated JS
// bundle, plus reCAPTCHA-gating on top (confirmed against multiple current
// third-party scrapers, mid-2026) - a plain fetch() with static headers gets
// 403/406. Instead of reimplementing their token signing (which would break
// the moment they rotate it again), we drive a real headless Chromium to the
// public search-results page, let Naukri's own JS make its own correctly-signed
// call, and capture that network response. Same approach for job detail pages:
// we read the rendered page rather than guessing a second bare API.
//
// Because Naukri's raw JSON field names aren't publicly documented and have
// shifted before, normalization below is defensive: it tries the flat field
// names most commonly observed, then falls back to scanning Naukri's
// `placeholders`-array shape (type/label pairs) seen in older API responses.

import { chromium, type Browser, type Page } from "playwright"

export function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

export async function withBrowser<T>(fn: (page: Page) => Promise<T>): Promise<T> {
  let browser: Browser | null = null
  try {
    browser = await chromium.launch({
      headless: true,
      // --no-sandbox: containers rarely have the user-namespace setup
      // Chromium's sandbox wants; --disable-dev-shm-usage: /dev/shm is
      // tiny by default in Docker and Chromium can crash without this.
      args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    })
    const context = await browser.newContext({
      userAgent: UA,
      viewport: { width: 1366, height: 900 },
      locale: "en-IN",
    })
    const page = await context.newPage()
    return await fn(page)
  } finally {
    if (browser) await browser.close()
  }
}

/** Slugifies a free-text keyword/location the way Naukri's own SEO URLs do. */
export function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
}

/** Builds a Naukri SEO search-results URL for a given page number. */
export function buildSearchUrl(query: string | undefined, location: string | undefined, page: number): string {
  const parts: string[] = []
  if (query) parts.push(slugify(query))
  parts.push("jobs")
  if (location) parts.push("in", slugify(location))
  let slug = parts.join("-")
  if (page > 1) slug += `-${page}`
  return `https://www.naukri.com/${slug}`
}

export interface RawSearchResponse {
  jobDetails?: unknown[]
  [k: string]: unknown
}

export interface NormalizedJob {
  id: string
  title: string
  company: string | null
  location: string | null
  experienceText: string | null
  experienceMinYears: number | null
  experienceMaxYears: number | null
  salary: string | null
  skills: string[]
  postedText: string | null
  postedDaysAgo: number | null
  url: string
}

function firstString(...vals: unknown[]): string | null {
  for (const v of vals) {
    if (typeof v === "string" && v.trim()) return v.trim()
  }
  return null
}

/** Naukri's older API shape nests experience/location/salary inside a
 * `placeholders: [{type, label}]` array rather than flat fields. Scan for it
 * as a fallback when the flat field is missing. */
function fromPlaceholders(item: Record<string, unknown>, type: string): string | null {
  const placeholders = item.placeholders
  if (!Array.isArray(placeholders)) return null
  for (const p of placeholders) {
    if (p && typeof p === "object" && (p as any).type === type && typeof (p as any).label === "string") {
      return (p as any).label
    }
  }
  return null
}

/** Parses "2-5 Yrs", "0-1 Yrs", "5+ Yrs" into [min, max] years. */
export function parseExperienceRange(text: string | null): [number | null, number | null] {
  if (!text) return [null, null]
  const range = text.match(/(\d+)\s*-\s*(\d+)/)
  if (range) return [parseInt(range[1], 10), parseInt(range[2], 10)]
  const plus = text.match(/(\d+)\s*\+/)
  if (plus) return [parseInt(plus[1], 10), null]
  const single = text.match(/(\d+)/)
  if (single) return [parseInt(single[1], 10), parseInt(single[1], 10)]
  return [null, null]
}

/** Parses Naukri's relative posted-date strings ("Today", "2 Days Ago",
 * "30+ Days Ago") into an approximate day count. Returns null if unparsable
 * (treated as "unknown age", never filtered out by a --jobage filter). */
export function parsePostedDaysAgo(text: string | null): number | null {
  if (!text) return null
  const t = text.trim().toLowerCase()
  if (t === "today" || t === "just now") return 0
  if (t === "yesterday") return 1
  const days = t.match(/(\d+)\s*\+?\s*day/)
  if (days) return parseInt(days[1], 10)
  const hours = t.match(/(\d+)\s*hour/)
  if (hours) return 0
  const months = t.match(/(\d+)\s*month/)
  if (months) return parseInt(months[1], 10) * 30
  return null
}

export function normalizeJob(raw: unknown): NormalizedJob | null {
  if (!raw || typeof raw !== "object") return null
  const item = raw as Record<string, unknown>

  const id = firstString(item.jobId, item.id, item.jdId)
  const title = firstString(item.title, item.jobTitle, item.designation)
  if (!id || !title) return null

  const company = firstString(item.companyName, item.companyname, fromPlaceholders(item, "companyName"))
  const location = firstString(item.location, item.jobLocation, fromPlaceholders(item, "location"))
  const experienceText = firstString(item.experience, item.experienceText, fromPlaceholders(item, "experience"))
  const [experienceMinYears, experienceMaxYears] = parseExperienceRange(experienceText)
  const salary = firstString(item.salary, item.ctc, fromPlaceholders(item, "salary"))
  const postedText = firstString(item.postedDate, item.createdDate, item.footerPlaceholderLabel)
  const postedDaysAgo = parsePostedDaysAgo(postedText)

  let skills: string[] = []
  if (Array.isArray(item.tagsAndSkills)) {
    skills = item.tagsAndSkills.filter((s): s is string => typeof s === "string")
  } else if (typeof item.tagsAndSkills === "string") {
    skills = item.tagsAndSkills.split(",").map((s) => s.trim()).filter(Boolean)
  }

  const rawUrl = firstString(item.jdURL, item.jobUrl, item.staticUrl, item.landingPageUrl)
  const url = rawUrl
    ? rawUrl.startsWith("http")
      ? rawUrl
      : `https://www.naukri.com${rawUrl.startsWith("/") ? "" : "/"}${rawUrl}`
    : `https://www.naukri.com/job-listings-${id}`

  return {
    id,
    title,
    company,
    location,
    experienceText,
    experienceMinYears,
    experienceMaxYears,
    salary,
    skills,
    postedText,
    postedDaysAgo,
    url,
  }
}

/** Extracts schema.org JobPosting JSON-LD if present - many job boards
 * (including Naukri) embed this for Google Jobs indexing. Same technique
 * jobdanmark-search's detail command already uses; more stable than a CSS
 * class name, which Naukri's React build hashes and rotates on every deploy. */
export function extractJobPostingJsonLd(html: string): Record<string, unknown> | null {
  const matches = html.matchAll(/<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)
  for (const m of matches) {
    try {
      const data = JSON.parse(m[1])
      const candidates = Array.isArray(data) ? data : [data]
      for (const c of candidates) {
        if (c && typeof c === "object" && (c as any)["@type"] === "JobPosting") {
          return c as Record<string, unknown>
        }
      }
    } catch {
      // malformed JSON-LD block - skip it, try the next script tag
    }
  }
  return null
}

function stripTags(html: string): string {
  return html
    .replace(/<\s*br\s*\/?>/gi, "\n")
    .replace(/<\/(p|li|ul|ol|div|h\d)>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

export function htmlToText(html: string): string {
  return stripTags(html)
}
