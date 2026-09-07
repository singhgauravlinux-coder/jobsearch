import { test, expect, describe } from "bun:test"
import {
  slugify,
  buildSearchUrl,
  parseExperienceRange,
  parsePostedDaysAgo,
  normalizeJob,
  extractJobPostingJsonLd,
  htmlToText,
} from "../src/helpers.js"

describe("slugify", () => {
  test("lowercases and hyphenates", () => {
    expect(slugify("Site Reliability Engineer")).toBe("site-reliability-engineer")
  })
  test("strips punctuation", () => {
    expect(slugify("C++ / Go Developer!")).toBe("c-go-developer")
  })
})

describe("buildSearchUrl", () => {
  test("query + location, page 1", () => {
    expect(buildSearchUrl("data engineer", "Bengaluru", 1)).toBe(
      "https://www.naukri.com/data-engineer-jobs-in-bengaluru",
    )
  })
  test("query only", () => {
    expect(buildSearchUrl("data engineer", undefined, 1)).toBe("https://www.naukri.com/data-engineer-jobs")
  })
  test("page > 1 appends page number", () => {
    expect(buildSearchUrl("data engineer", "Pune", 3)).toBe("https://www.naukri.com/data-engineer-jobs-in-pune-3")
  })
})

describe("parseExperienceRange", () => {
  test("range", () => {
    expect(parseExperienceRange("2-5 Yrs")).toEqual([2, 5])
  })
  test("plus", () => {
    expect(parseExperienceRange("5+ Yrs")).toEqual([5, null])
  })
  test("single number", () => {
    expect(parseExperienceRange("3 Yrs")).toEqual([3, 3])
  })
  test("null input", () => {
    expect(parseExperienceRange(null)).toEqual([null, null])
  })
})

describe("parsePostedDaysAgo", () => {
  test("today", () => expect(parsePostedDaysAgo("Today")).toBe(0))
  test("yesterday", () => expect(parsePostedDaysAgo("Yesterday")).toBe(1))
  test("N days ago", () => expect(parsePostedDaysAgo("5 Days Ago")).toBe(5))
  test("30+ days ago", () => expect(parsePostedDaysAgo("30+ Days Ago")).toBe(30))
  test("hours ago treated as today", () => expect(parsePostedDaysAgo("3 hours ago")).toBe(0))
  test("unparsable returns null", () => expect(parsePostedDaysAgo("sometime")).toBe(null))
})

describe("normalizeJob", () => {
  test("flat field shape", () => {
    const job = normalizeJob({
      jobId: "123456",
      title: "Backend Engineer",
      companyName: "Acme Corp",
      location: "Bengaluru",
      experience: "2-5 Yrs",
      salary: "10-15 Lacs PA",
      tagsAndSkills: "Python, Django, AWS",
      postedDate: "2 Days Ago",
      jdURL: "/job-listings-backend-engineer-acme-123456",
    })
    expect(job).not.toBeNull()
    expect(job!.title).toBe("Backend Engineer")
    expect(job!.company).toBe("Acme Corp")
    expect(job!.experienceMinYears).toBe(2)
    expect(job!.experienceMaxYears).toBe(5)
    expect(job!.skills).toEqual(["Python", "Django", "AWS"])
    expect(job!.postedDaysAgo).toBe(2)
    expect(job!.url).toBe("https://www.naukri.com/job-listings-backend-engineer-acme-123456")
  })

  test("placeholders-array fallback shape", () => {
    const job = normalizeJob({
      jobId: "789",
      title: "QA Engineer",
      placeholders: [
        { type: "location", label: "Hyderabad" },
        { type: "experience", label: "0-1 Yrs" },
      ],
    })
    expect(job).not.toBeNull()
    expect(job!.location).toBe("Hyderabad")
    expect(job!.experienceText).toBe("0-1 Yrs")
  })

  test("missing id or title returns null", () => {
    expect(normalizeJob({ title: "No ID here" })).toBeNull()
    expect(normalizeJob({ jobId: "1" })).toBeNull()
  })
})

describe("extractJobPostingJsonLd", () => {
  test("finds a JobPosting block among other JSON-LD", () => {
    const html = `
      <script type="application/ld+json">{"@type":"BreadcrumbList"}</script>
      <script type="application/ld+json">{"@type":"JobPosting","title":"Backend Engineer","description":"<p>Great role</p>"}</script>
    `
    const jsonLd = extractJobPostingJsonLd(html)
    expect(jsonLd).not.toBeNull()
    expect(jsonLd!.title).toBe("Backend Engineer")
  })

  test("returns null when absent", () => {
    expect(extractJobPostingJsonLd("<html><body>no scripts here</body></html>")).toBeNull()
  })

  test("skips malformed JSON-LD blocks without throwing", () => {
    const html = `<script type="application/ld+json">{not valid json</script>`
    expect(extractJobPostingJsonLd(html)).toBeNull()
  })
})

describe("htmlToText", () => {
  test("strips tags and decodes entities", () => {
    expect(htmlToText("<p>Great role &amp; team</p><br>Apply now")).toBe("Great role & team\n\nApply now")
  })
})
