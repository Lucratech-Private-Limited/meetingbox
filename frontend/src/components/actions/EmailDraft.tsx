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
        <label className="block text-sm font-medium text-gray-700 mb-1">To</label>
        <div className="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-900">
          {draft.to}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Subject</label>
        <div className="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-900">
          {draft.subject}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Message</label>
        <div className="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-700 whitespace-pre-wrap">
          {draft.body}
        </div>
      </div>

      {draft.context && (
        <div className="pt-4 border-t border-gray-200">
          <details>
            <summary className="text-sm font-medium text-gray-700 cursor-pointer">
              Meeting context
            </summary>
            <div className="mt-2 text-sm text-gray-600 whitespace-pre-wrap">
              {draft.context}
            </div>
          </details>
        </div>
      )}
    </div>
  )
}
