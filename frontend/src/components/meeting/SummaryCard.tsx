// Renders the AI-generated meeting summary with topics, decisions, and action items

import type { MeetingSummary } from '../../types/meeting'

interface SummaryCardProps {
  summary: MeetingSummary | null
}

export default function SummaryCard({ summary }: SummaryCardProps) {
  if (!summary) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <p className="text-gray-500">No summary available yet. The meeting is still being processed.</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">

      {/* Main Summary */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Summary</h3>
        <p className="text-gray-700 leading-relaxed">{summary.summary}</p>
      </div>

      {/* Key Topics */}
      {summary.topics?.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Key Topics</h3>
          <div className="flex flex-wrap gap-2">
            {summary.topics.map((topic) => (
              <span
                key={topic}
                className="inline-flex items-center px-3 py-1 rounded-full text-sm bg-primary-50 text-primary-700"
              >
                {topic}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Decisions Made */}
      {summary.decisions?.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Decisions Made</h3>
          <ul className="space-y-3">
            {summary.decisions.map((decision) => (
              <li key={decision} className="flex items-start">
                <svg className="w-5 h-5 text-green-500 mr-3 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
                <span className="text-gray-700">{decision}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Action Items */}
      {summary.action_items?.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Action Items</h3>
          <ul className="space-y-4">
            {summary.action_items.map((item) => (
              <li key={item.task} className="flex items-start">
                <input
                  type="checkbox"
                  checked={item.completed}
                  readOnly
                  className="mt-1 mr-3 h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
                />
                <div className="flex-1">
                  <p className={`text-gray-900 ${item.completed ? 'line-through' : ''}`}>
                    {item.task}
                  </p>
                  {(item.assignee || item.due_date) && (
                    <div className="mt-1 flex items-center space-x-4 text-sm text-gray-500">
                      {item.assignee && (
                        <span className="flex items-center">
                          <svg className="w-4 h-4 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                          </svg>
                          {item.assignee}
                        </span>
                      )}
                      {item.due_date && (
                        <span className="flex items-center">
                          <svg className="w-4 h-4 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                          </svg>
                          {item.due_date}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Sentiment */}
      {summary.sentiment && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Meeting Sentiment</h3>
          <p className="text-gray-700">{summary.sentiment}</p>
        </div>
      )}
    </div>
  )
}
