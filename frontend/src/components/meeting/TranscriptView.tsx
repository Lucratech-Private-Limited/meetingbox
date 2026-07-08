// Searchable transcript viewer with optional timestamps

import { useState } from 'react'
import type { TranscriptSegment } from '../../types/meeting'

interface TranscriptViewProps {
  segments: TranscriptSegment[]
}

export default function TranscriptView({ segments }: TranscriptViewProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [showTimestamps, setShowTimestamps] = useState(true)

  const formatTimestamp = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const filteredSegments = segments.filter((segment) =>
    segment.text.toLowerCase().includes(searchQuery.toLowerCase())
  )

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6">

      {/* Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6 pb-4 border-b border-gray-200">
        <div className="flex-1 max-w-md">
          <div className="relative">
            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
              <svg className="h-5 w-5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <input
              type="text"
              placeholder="Search transcript..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="block w-full pl-10 pr-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent text-sm"
            />
          </div>
        </div>

        <label className="flex items-center">
          <input
            type="checkbox"
            checked={showTimestamps}
            onChange={(e) => setShowTimestamps(e.target.checked)}
            className="h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
          />
          <span className="ml-2 text-sm text-gray-700">Show timestamps</span>
        </label>
      </div>

      {/* Transcript body */}
      {filteredSegments.length === 0 ? (
        <p className="text-gray-500 text-center py-8">
          {searchQuery ? 'No matches found' : 'No transcript available'}
        </p>
      ) : (
        <div className="space-y-4 max-h-[32rem] overflow-y-auto">
          {filteredSegments.map((segment) => (
            <div key={segment.segment_num} className="flex items-start space-x-3">
              {showTimestamps && (
                <span className="text-xs text-gray-400 font-mono mt-1 flex-shrink-0 w-12">
                  {formatTimestamp(segment.start_time)}
                </span>
              )}
              <div className="flex-1">
                {segment.speaker_id && (
                  <span className="text-sm font-medium text-gray-900 mr-2">
                    Speaker {segment.speaker_id}:
                  </span>
                )}
                <span className="text-gray-700">
                  {searchQuery ? (
                    <HighlightedText text={segment.text} query={searchQuery} />
                  ) : (
                    segment.text
                  )}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Highlight matching search terms in transcript text */
function HighlightedText({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>
  const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')
  const parts = text.split(regex)
  return (
    <>
      {parts.map((part, i) =>
        regex.test(part) ? (
          <mark key={i} className="bg-yellow-200">{part}</mark>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  )
}
