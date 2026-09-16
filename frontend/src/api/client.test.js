import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import { ApiError, apiGet, apiPost } from './client'

it('returns parsed JSON on success', async () => {
  server.use(http.get('/api/stats', () => HttpResponse.json({ counts: { new: 2 } })))
  expect(await apiGet('/stats')).toEqual({ counts: { new: 2 } })
})

it('surfaces validator errors from a 422 instead of throwing a generic error', async () => {
  server.use(http.post('/api/outreach/1/edit', () =>
    HttpResponse.json({ errors: ['contains a semicolon'] }, { status: 422 })))
  await expect(apiPost('/outreach/1/edit', {})).rejects.toMatchObject({
    status: 422, errors: ['contains a semicolon'],
  })
})

it('carries the running job id out of a 409', async () => {
  server.use(http.post('/api/jobs/scrape', () =>
    HttpResponse.json({ job_id: 7 }, { status: 409 })))
  await expect(apiPost('/jobs/scrape')).rejects.toMatchObject({ status: 409, job_id: 7 })
})

it('never swallows a network failure silently', async () => {
  server.use(http.get('/api/stats', () => HttpResponse.error()))
  await expect(apiGet('/stats')).rejects.toBeInstanceOf(Error)
})

describe('beyond the brief', () => {
  it('rejects a 404 with status 404', async () => {
    server.use(http.get('/api/queue/999', () => HttpResponse.json({ detail: 'no such outreach' }, { status: 404 })))
    await expect(apiGet('/queue/999')).rejects.toMatchObject({ status: 404 })
  })

  it('rejects a 500 without exposing the body as if it were data', async () => {
    server.use(http.get('/api/stats', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })))
    const err = await apiGet('/stats').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(500)
  })

  it('apiPost sends Content-Type: application/json and a JSON body', async () => {
    let seenContentType
    let seenBody
    server.use(http.post('/api/send', async ({ request }) => {
      seenContentType = request.headers.get('content-type')
      seenBody = await request.json()
      return HttpResponse.json({ sent: 1 })
    }))
    await apiPost('/send', { limit: 5 })
    expect(seenContentType).toContain('application/json')
    expect(seenBody).toEqual({ limit: 5 })
  })

  it('resolves a 204/empty body to null instead of throwing on .json()', async () => {
    server.use(http.post('/api/outreach/1/approve', () => new HttpResponse(null, { status: 204 })))
    await expect(apiPost('/outreach/1/approve')).resolves.toBeNull()
  })
})
