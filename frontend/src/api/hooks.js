// TanStack Query hooks over ./client.js - the one seam Tasks 15-17's
// screens are built on. Query keys are plain arrays so invalidation below
// can target them precisely; defaults (retry: 1, staleTime: 5000) are set
// once here rather than per-call.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost } from './client'

const QUERY_DEFAULTS = { retry: 1, staleTime: 5000 }

// ---- queries ----------------------------------------------------------

export function useStats() {
  return useQuery({ queryKey: ['stats'], queryFn: () => apiGet('/stats'), ...QUERY_DEFAULTS })
}

export function useQueue() {
  return useQuery({ queryKey: ['queue'], queryFn: () => apiGet('/queue'), ...QUERY_DEFAULTS })
}

export function useQueueItem(id) {
  return useQuery({
    queryKey: ['queue', id],
    queryFn: () => apiGet(`/queue/${id}`),
    enabled: id != null,
    ...QUERY_DEFAULTS,
  })
}

export function useManual() {
  return useQuery({ queryKey: ['manual'], queryFn: () => apiGet('/manual'), ...QUERY_DEFAULTS })
}

export function useJobs() {
  return useQuery({ queryKey: ['jobs'], queryFn: () => apiGet('/jobs'), ...QUERY_DEFAULTS })
}

function contactsQueryString(filters = {}) {
  const params = new URLSearchParams()
  for (const key of ['state', 'hiring', 'batch', 'email_status']) {
    const value = filters[key]
    if (value !== undefined && value !== null && value !== '') params.set(key, value)
  }
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

export function useContacts(filters = {}) {
  return useQuery({
    queryKey: ['contacts', filters],
    queryFn: () => apiGet(`/contacts${contactsQueryString(filters)}`),
    ...QUERY_DEFAULTS,
  })
}

export function useThread(id) {
  return useQuery({
    queryKey: ['thread', id],
    queryFn: () => apiGet(`/outreach/${id}/thread`),
    enabled: id != null,
    ...QUERY_DEFAULTS,
  })
}

// ---- mutations ----------------------------------------------------------
// Each mutation invalidates exactly the queries its write affects on the
// backend - see the routers' own docstrings (queue.py, manual.py,
// jobs.py, thread.py) for which state each route actually changes.

export function useApprove() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id) => apiPost(`/outreach/${id}/approve`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

export function useEdit() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, subject, body }) => apiPost(`/outreach/${id}/edit`, { subject, body }),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['queue', id] })
    },
  })
}

export function useReject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id) => apiPost(`/outreach/${id}/reject`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

export function useRedraft() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id) => apiPost(`/outreach/${id}/redraft`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

export function useSetEmail() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, address, giveUp }) =>
      apiPost(`/outreach/${id}/email`, giveUp ? { give_up: true } : { address }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['manual'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

export function useSend() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (limit) => apiPost('/send', limit != null ? { limit } : undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      queryClient.invalidateQueries({ queryKey: ['queue'] })
    },
  })
}

export function useStartJob() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ kind, createdBy }) =>
      apiPost(`/jobs/${kind}`, createdBy != null ? { created_by: createdBy } : undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useSetLabel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ messageId, label }) => apiPost(`/messages/${messageId}/label`, { label }),
    // threadId is a caller-supplied mutation variable (the backend's
    // OkOut response carries no outreach/thread id to invalidate by) -
    // onSuccess's second argument is always the variables object passed
    // to mutate(), per TanStack Query's mutation contract.
    onSuccess: (_data, { threadId }) => {
      queryClient.invalidateQueries({ queryKey: ['thread', threadId] })
    },
  })
}
