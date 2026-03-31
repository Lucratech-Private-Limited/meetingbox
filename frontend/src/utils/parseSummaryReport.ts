/**
 * Split composed meeting report text (web/API) into UI sections.
 * Matches backend _compose_stored_report_body markers and DETAILED ACCOUNT headings.
 */

export interface ParsedMeetingReport {
  /** Narrative before DETAILED ACCOUNT (and before OPEN QUESTIONS / RISKS blocks). */
  overview: string
  /** Body after a DETAILED ACCOUNT heading (when present). */
  detailedAccount: string
  openQuestions: string[]
  risksConcerns: string[]
}

const RISKS_SPLIT = /\r?\n\r?\n---\r?\nRISKS\s*\/\s*CONCERNS\r?\n/i
const OPEN_SPLIT = /\r?\n\r?\n---\r?\nOPEN QUESTIONS\r?\n/i
// Heading inserted by the model; allow one or two newlines before it
const DETAILED_SPLIT = /\r?\n(?:\r?\n)?DETAILED ACCOUNT\s*\r?\n/i

function splitBulletBlock(block: string): string[] {
  const lines = block.trim().split(/\r?\n/)
  const out: string[] = []
  for (const line of lines) {
    const t = line.replace(/^\s*[•\-*]\s*/, '').trim()
    if (t) out.push(t)
  }
  return out.length > 0 ? out : block.trim() ? [block.trim()] : []
}

/**
 * First segment: text before DETAILED ACCOUNT; second: after (rest of main body before --- sections).
 */
function splitDetailedAccount(mainPart: string): { overview: string; detailed: string } {
  const m = mainPart.match(DETAILED_SPLIT)
  if (!m || m.index === undefined) {
    return { overview: mainPart.trim(), detailed: '' }
  }
  const idx = m.index
  const overview = mainPart.slice(0, idx).trim()
  const detailed = mainPart.slice(idx + m[0].length).trim()
  return { overview, detailed }
}

export function parseSummaryReport(fullText: string): ParsedMeetingReport {
  const t = fullText ?? ''
  let beforeRisks = t
  let risksBody = ''

  const risksParts = t.split(RISKS_SPLIT)
  if (risksParts.length > 1) {
    beforeRisks = risksParts[0] ?? ''
    risksBody = (risksParts[1] ?? '').trim()
  }

  let mainPart = beforeRisks.trim()
  let openBody = ''

  const openParts = beforeRisks.split(OPEN_SPLIT)
  if (openParts.length > 1) {
    mainPart = (openParts[0] ?? '').trim()
    openBody = (openParts[1] ?? '').trim()
  }

  const { overview, detailed } = splitDetailedAccount(mainPart)

  return {
    overview,
    detailedAccount: detailed,
    openQuestions: openBody ? splitBulletBlock(openBody) : [],
    risksConcerns: risksBody ? splitBulletBlock(risksBody) : [],
  }
}
