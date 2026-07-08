import client from './client'

export type CommitmentStatusParam =
  | ''
  | 'active'
  | 'completed'
  | 'snoozed'
  | 'cancelled'
  | 'all'

export interface CommitmentRow {
  id: string
  user_id?: string
  title: string
  detail?: string | null
  status?: string
  tags?: string
  remind_at?: string | null
  due_at?: string | null
  source?: string | null
  calendar_event_id?: string | null
  created_at?: string
  updated_at?: string
}

export const commitmentsApi = {
  list: async (params?: {
    status?: CommitmentStatusParam
    limit?: number
  }): Promise<{ commitments: CommitmentRow[]; count: number }> => {
    const response = await client.get<{ commitments: CommitmentRow[]; count: number }>('/api/commitments', {
      params: params?.status
        ? { status: params.status, limit: params.limit }
        : { limit: params?.limit },
    })
    const data = response.data
    return {
      commitments: data?.commitments ?? [],
      count: data?.count ?? 0,
    }
  },
}

/** Convenience for callers that pass a free-form status string (e.g. Dashboard). */
export async function listCommitments(
  status: string = '',
  limit: number = 40
): Promise<{ commitments: CommitmentRow[]; count: number }> {
  const trimmed = status.trim()
  if (trimmed) {
    return commitmentsApi.list({
      status: trimmed as CommitmentStatusParam,
      limit,
    })
  }
  return commitmentsApi.list({ limit })
}
