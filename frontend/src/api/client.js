// Thin fetch wrapper around the FastAPI backend mounted at /api (proxied
// to http://127.0.0.1:8000 in dev by vite.config.js). Every non-2xx
// response is turned into a thrown ApiError carrying the status code and,
// for the shapes the backend actually sends, the extra fields the screens
// need verbatim:
//   - 422 from queue/manual routes: {errors: [...]}  -> err.errors
//   - 409 from POST /api/jobs/{kind}: {job_id}        -> err.job_id
// A network failure (fetch rejects, e.g. HttpResponse.error() in tests, or
// the backend being down) is never swallowed: it propagates as a plain
// Error, so callers/react-query see a rejected promise either way.

export class ApiError extends Error {
  constructor(status, body) {
    const message = (body && typeof body === 'object' && 'detail' in body)
      ? String(body.detail)
      : `request failed with status ${status}`
    super(message)
    this.name = 'ApiError'
    this.status = status
    if (body && typeof body === 'object') {
      if (Array.isArray(body.errors)) this.errors = body.errors
      if ('job_id' in body) this.job_id = body.job_id
    }
  }
}

async function parseBody(response) {
  // A 204 (or any response with no body, e.g. the approve/reject/redraft
  // OkOut routes when a test stubs an empty response) has nothing for
  // .json() to parse - calling it anyway throws a SyntaxError. Read the
  // text first and only parse when there is something there.
  const text = await response.text()
  if (text.length === 0) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

async function request(path, { method = 'GET', body } = {}) {
  const init = { method, headers: {} }
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }

  // Network-level failures (DNS, connection refused, MSW's
  // HttpResponse.error()) reject fetch() itself with a plain Error/
  // TypeError - let that propagate unmodified rather than wrapping it, so
  // it is never mistaken for a server response.
  const response = await fetch(`/api${path}`, init)
  const data = await parseBody(response)

  if (!response.ok) {
    throw new ApiError(response.status, data)
  }
  return data
}

export function apiGet(path) {
  return request(path, { method: 'GET' })
}

export function apiPost(path, body) {
  return request(path, { method: 'POST', body })
}
