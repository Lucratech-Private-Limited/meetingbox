// Displays an AI-drafted email for review before approval

interface EmailDraftProps {
  draft: {
    to: string
    subject: string
    body: string
    context?: string
  }
}

export default function EmailDraft({ draft }: EmailDraftProps) {
  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-app-ink-muted mb-1">To</label>
        <div className="px-3 py-2 bg-app-page border border-app-border rounded-lg text-sm text-app-ink">
          {draft.to}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-app-ink-muted mb-1">Subject</label>
        <div className="px-3 py-2 bg-app-page border border-app-border rounded-lg text-sm text-app-ink">
          {draft.subject}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-app-ink-muted mb-1">Message</label>
        <div className="px-3 py-2 bg-app-page border border-app-border rounded-lg text-sm text-app-ink-muted whitespace-pre-wrap">
          {draft.body}
        </div>
      </div>

      {draft.context && (
        <div className="pt-4 border-t border-app-border">
          <details>
            <summary className="text-sm font-medium text-app-ink-muted cursor-pointer">
              Meeting context
            </summary>
            <div className="mt-2 text-sm text-app-ink-muted whitespace-pre-wrap">
              {draft.context}
            </div>
          </details>
        </div>
      )}
    </div>
  )
}
