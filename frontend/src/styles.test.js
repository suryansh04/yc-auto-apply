// Task 18: structural assertions on the hand-written stylesheet - no
// pixel snapshots. Pins the two constraints the brief calls out
// (tokens on bare :root, a real side gutter at phone width) plus a third
// closing the "defined only inside a media query" failure mode the first
// two don't catch on their own.
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync('src/styles.css', 'utf8')
const reviewQueueCss = readFileSync('src/styles/review-queue.css', 'utf8')
const threadCss = readFileSync('src/styles/thread.css', 'utf8')

// Strips /* ... */ comments before a block of CSS is matched against a
// declaration regex, so an explanatory comment that happens to mention a
// property/value pair in prose (e.g. "min-width: 0 lets the pane shrink")
// can't stand in for the real declaration once it's deleted.
function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '')
}

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

  // The subset check above (dark ⊆ light) does not catch the dark palette
  // being *incomplete* - e.g. deleting `--danger` from the dark block
  // still passes it, and the app silently falls back to the light-mode
  // red (#b3261e) on a dark background for exactly the elements
  // (.banner-error, .field-error, .editor-errors) that most need contrast.
  // Set equality closes that: every light token must also be re-declared
  // in the dark block, not just "nothing extra was added".
  it('the dark block re-declares every light token (the dark palette is complete)', () => {
    const lightTokens = [...light.matchAll(/--[\w-]+(?=\s*:)/g)].map((m) => m[0])
    const darkTokens = [...dark.matchAll(/--[\w-]+(?=\s*:)/g)].map((m) => m[0])
    expect(lightTokens.length).toBeGreaterThan(0)
    expect(new Set(darkTokens)).toEqual(new Set(lightTokens))
  })

  // The brief says the dark variant is "defined by re-declaring tokens
  // only" - the dark :root body should contain nothing but
  // `--token: value;` custom-property declarations plus `color-scheme`.
  // A stray non-token rule (e.g. `body { background: #000 }`) sitting
  // inside the @media block would not be caught by the token checks
  // above, since they only ever look for `--*` declarations.
  it('the dark block contains only custom-property declarations and color-scheme', () => {
    // `dark` is everything inside the @media block's braces, which is
    // itself a nested `:root { ... }` rule - strip that wrapper down to
    // just its declarations before checking each one.
    const innerStart = dark.indexOf('{')
    const innerEnd = dark.lastIndexOf('}')
    const declarations = dark.slice(innerStart + 1, innerEnd)
    const body = declarations
      .replace(/color-scheme:\s*dark;/, '')
      .split(';')
      .map((decl) => decl.trim())
      .filter(Boolean)
    expect(body.length).toBeGreaterThan(0)
    for (const decl of body) {
      expect(decl).toMatch(/^--[\w-]+\s*:/)
    }
  })

  it('every state colour token used by the app is present in the light palette', () => {
    for (const token of ['--ok', '--warn']) {
      expect(light).toContain(token)
    }
  })
})

describe('long unbroken content wraps instead of being clipped', () => {
  // html, body are overflow-x: hidden (styles.css) so a pane or message
  // that gets forced wider than its column by a long URL doesn't show a
  // scrollbar - it silently clips. These two rules are what actually
  // prevent the widening: overflow-wrap lets the text itself break, and
  // min-width: 0 lets the grid/flex item shrink below its content's
  // intrinsic width in the first place.
  it('.wire-text and .message-body break long unbroken text instead of clipping it', () => {
    const wireText = stripComments(
      reviewQueueCss.slice(
        reviewQueueCss.indexOf('.wire-text {'),
        reviewQueueCss.indexOf('}', reviewQueueCss.indexOf('.wire-text {'))
      )
    )
    const messageBody = stripComments(
      threadCss.slice(
        threadCss.indexOf('.message-body {'),
        threadCss.indexOf('}', threadCss.indexOf('.message-body {'))
      )
    )
    expect(wireText).toMatch(/overflow-wrap:\s*anywhere\s*;/)
    expect(messageBody).toMatch(/overflow-wrap:\s*anywhere\s*;/)
  })

  it('the review-queue panes and thread message can shrink below their content width', () => {
    const panes = stripComments(
      reviewQueueCss.slice(
        reviewQueueCss.indexOf('.draft-pane,'),
        reviewQueueCss.indexOf('}', reviewQueueCss.indexOf('.draft-pane,'))
      )
    )
    const message = stripComments(
      threadCss.slice(
        threadCss.indexOf('.message {'),
        threadCss.indexOf('}', threadCss.indexOf('.message {'))
      )
    )
    expect(panes).toMatch(/min-width:\s*0\s*;/)
    expect(message).toMatch(/min-width:\s*0\s*;/)
  })

  // V1 (T18 fix wave 2): a founder with several guessed-and-rejected
  // candidates made the cited-source block push Approve/Reject ~1100px
  // below the viewport. A capped, scrollable .source-text is what keeps
  // the actions reachable without scrolling the whole page.
  it('caps .source-text height and makes it scroll instead of pushing the page down', () => {
    const sourceText = stripComments(
      reviewQueueCss.slice(
        reviewQueueCss.indexOf('.source-text {'),
        reviewQueueCss.indexOf('}', reviewQueueCss.indexOf('.source-text {'))
      )
    )
    expect(sourceText).toMatch(/max-height:\s*\d+vh\s*;/)
    expect(sourceText).toMatch(/overflow-y:\s*auto\s*;/)
  })
})

describe('form controls use the 14px base, not the label caption size', () => {
  // Every text input, select and textarea in the app is nested inside a
  // <label>, and label { font-size: 0.85rem } (below). `font: inherit`
  // on the control pulls in that computed 0.85rem via inheritance, so
  // without an explicit override the draft body the operator *edits*
  // renders at 11.9px while the same text they *read* (.wire-text) is
  // 13.3px. `rem` always resolves against the root font-size (14px), so
  // an explicit `font-size: 1rem` on the control undoes the inherited
  // label size regardless of nesting.
  function ruleBlock(source, selectorStart) {
    const start = source.indexOf(selectorStart)
    const openBrace = source.indexOf('{', start)
    const closeBrace = source.indexOf('}', openBrace)
    return stripComments(source.slice(openBrace + 1, closeBrace))
  }

  it("input/select/textarea declare an explicit 14px-equivalent font-size, not just font: inherit", () => {
    const controls = ruleBlock(css, "input[type='text'],")
    expect(controls).toMatch(/font-size:\s*(1rem|14px)\s*;/)
  })

  it('button declares an explicit 14px-equivalent font-size', () => {
    const button = ruleBlock(css, 'button {')
    expect(button).toMatch(/font-size:\s*(1rem|14px)\s*;/)
  })

  it("the label caption stays at 0.85rem (its own rule is untouched)", () => {
    const label = ruleBlock(css, 'label {')
    expect(label).toMatch(/font-size:\s*0\.85rem/)
  })
})
