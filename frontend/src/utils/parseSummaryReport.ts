/**
 * Split composed meeting report text (web/API) into UI sections.
 * Matches backend _compose_stored_report_body (--- markers) and model output
 * from the summarize prompt: **DETAILED ACCOUNT**, **OPEN QUESTIONS**, **RISKS / CONCERNS**.
 */

export interface ParsedMeetingReport {
  /** Narrative before DETAILED ACCOUNT (and before OPEN QUESTIONS / RISKS blocks). */
  overview: string
  /** Body after a DETAILED ACCOUNT heading (when present). */
  detailedAccount: string
  openQuestions: string[]
  risksConcerns: string[]
}

/** Backend compose: \n\n---\nRISKS / CONCERNS\n — or model: \n\n**RISKS / CONCERNS**\n */
const RISKS_MARKER =
  /\r?\n\r?\n(?:---\r?\n)?\*{0,2}RISKS\s*\/\s*CONCERNS\*{0,2}\s*\r?\n/i

/** Backend: \n\n---\nOPEN QUESTIONS\n — or model: \n\n**OPEN QUESTIONS**\n */
const OPEN_MARKER =
  /\r?\n\r?\n(?:---\r?\n)?\*{0,2}OPEN QUESTIONS\*{0,2}\s*\r?\n/i

/** Model may use **DETAILED ACCOUNT** at line start or after a blank line. */
const DETAILED_SPLIT =
  /(?:^|\r?\n(?:\r?\n)?)\*{0,2}DETAILED ACCOUNT\*{0,2}\s*\r?\n/i

function splitOnLastMarker(text: string, marker: RegExp): [string, string] {
  const r = new RegExp(marker.source, marker.flags.includes('g') ? marker.flags : `${marker.flags}g`)
  let last: RegExpExecArray | null = null
  let m: RegExpExecArray | null
  r.lastIndex = 0
  while ((m = r.exec(text)) !== null) {
    last = m
  }
  if (!last) return [text, '']
  return [text.slice(0, last.index), text.slice(last.index + last[0].length)]
}

function splitBulletBlock(block: string): string[] {
  const lines = block.trim().split(/\r?\n/)
  const out: string[] = []
  for (const line of lines) {
    const t = line
      .replace(/^\s*(?:[•\-*]|\d+[.)])\s+/, '')
      .replace(/^\s*\[[ xX]\]\s*/, '')
      .trim()
    if (t) out.push(t)
  }
  return out.length > 0 ? out : block.trim() ? [block.trim()] : []
}

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

  const [beforeRisks, risksBody] = splitOnLastMarker(t, RISKS_MARKER)
  const [mainPart, openBody] = splitOnLastMarker(beforeRisks, OPEN_MARKER)

  const { overview, detailed } = splitDetailedAccount(mainPart)

  return {
    overview,
    detailedAccount: detailed,
    openQuestions: openBody ? splitBulletBlock(openBody) : [],
    risksConcerns: risksBody ? splitBulletBlock(risksBody) : [],
  }
}
