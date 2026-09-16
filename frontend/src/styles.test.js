// Task 18: structural assertions on the hand-written stylesheet - no
// pixel snapshots. Pins the two constraints the brief calls out
// (tokens on bare :root, a real side gutter at phone width) plus a third
// closing the "defined only inside a media query" failure mode the first
// two don't catch on their own.
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync('src/styles.css', 'utf8')

it('defines the full palette on :root, not only inside a media query', () => {
  const root = css.slice(css.indexOf(':root'), css.indexOf('}', css.indexOf(':root')))
  for (const token of ['--bg', '--fg', '--muted', '--accent', '--border', '--danger']) {
    expect(root).toContain(token)
  }
})

it('keeps a side gutter at phone width', () => {
  expect(css).toMatch(/padding-inline:\s*(1[0-9]|[2-9][0-9])px/)
})

describe('every colour token is declared on the light :root, not only in a media query', () => {
  // Re-parse the file into "the first :root block" (light, unconditional)
  // and "everything inside @media (prefers-color-scheme: dark) { ... }"
  // (the dark re-declaration). A token that shows up only in the second
  // block and never the first would work in a dark-mode browser and be
  // silently undefined (falls back to `initial`/inherited, not a colour)
  // everywhere else - exactly the bug this test exists to catch.
  function firstRootBlock(source) {
    const start = source.indexOf(':root')
    const openBrace = source.indexOf('{', start)
    const closeBrace = source.indexOf('}', openBrace)
    return source.slice(openBrace + 1, closeBrace)
  }

  function darkMediaBlock(source) {
    const mediaStart = source.indexOf('@media (prefers-color-scheme: dark)')
    if (mediaStart === -1) return ''
    const braceOpen = source.indexOf('{', mediaStart)
    // Balance braces from the media query's own opening brace to find
    // where the whole @media block closes (it contains a nested :root
    // rule, so a naive indexOf('}') would stop at the inner rule's own
    // close brace instead).
    let depth = 0
    let i = braceOpen
    for (; i < source.length; i += 1) {
      if (source[i] === '{') depth += 1
      if (source[i] === '}') {
        depth -= 1
        if (depth === 0) break
      }
    }
    return source.slice(braceOpen + 1, i)
  }

  const light = firstRootBlock(css)
  const dark = darkMediaBlock(css)

  it('the dark block only re-declares tokens the light :root already defines', () => {
    expect(dark.length).toBeGreaterThan(0)
    const darkTokens = [...dark.matchAll(/--[\w-]+(?=\s*:)/g)].map((m) => m[0])
    expect(darkTokens.length).toBeGreaterThan(0)
    for (const token of darkTokens) {
      expect(light).toContain(`${token}:`)
    }
  })

  it('every state colour token used by the app is present in the light palette', () => {
    for (const token of ['--ok', '--warn']) {
      expect(light).toContain(token)
    }
  })
})
