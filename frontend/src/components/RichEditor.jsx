// The letter editor, with formatting.
//
// Operator request (2026-09-23): "give option such as bolding, italic,
// increasing size of a particular text, numerical or bullet points and so
// on so that i can really customize it".
//
// Tiptap (MIT, ProseMirror underneath) rather than a hand-rolled
// contenteditable: selection, undo and list editing are the parts that
// are easy to get almost right, and "almost right" in a letter editor
// means losing someone's text.
//
// Only what the server keeps is offered. `app/domain/rich_text.py` is an
// allowlist - bold, italic, underline, four text sizes, bullet and
// numbered lists - so every button here produces markup that survives
// the save. StarterKit's headings, quotes, code, links and rules are
// switched off rather than left in to be silently stripped: a control
// that does nothing on save is worse than no control.
import { useEditor, EditorContent, useEditorState } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { FontSize, TextStyle } from '@tiptap/extension-text-style'
import {
  Bold,
  Italic,
  List,
  ListOrdered,
  Redo2,
  Underline as UnderlineIcon,
  Undo2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

// Must match `FONT_SIZES` in app/domain/rich_text.py; anything else is
// dropped on save. "Normal" is no size at all - the reader's default.
const SIZES = [
  { value: '13px', label: 'Small' },
  { value: 'normal', label: 'Normal' },
  { value: '20px', label: 'Large' },
  { value: '24px', label: 'Larger' },
]

function ToolbarButton({ label, active, disabled, onClick, children }) {
  return (
    <Button
      type="button"
      size="icon"
      variant={active ? 'secondary' : 'ghost'}
      className="size-8"
      aria-label={label}
      aria-pressed={active ?? undefined}
      title={label}
      disabled={disabled}
      // Keep the selection: a mousedown on a button would otherwise move
      // focus out of the editor before the command runs on it.
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
    >
      {children}
    </Button>
  )
}

function Toolbar({ editor }) {
  const state = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e.isActive('bold'),
      italic: e.isActive('italic'),
      underline: e.isActive('underline'),
      bullets: e.isActive('bulletList'),
      numbers: e.isActive('orderedList'),
      size: e.getAttributes('textStyle').fontSize ?? 'normal',
      canUndo: e.can().undo(),
      canRedo: e.can().redo(),
    }),
  })
  const chain = () => editor.chain().focus()

  return (
    <div
      role="toolbar"
      aria-label="Formatting"
      className="bg-muted/40 flex flex-wrap items-center gap-1 border-b p-1"
    >
      <ToolbarButton label="Bold" active={state.bold} onClick={() => chain().toggleBold().run()}>
        <Bold />
      </ToolbarButton>
      <ToolbarButton label="Italic" active={state.italic} onClick={() => chain().toggleItalic().run()}>
        <Italic />
      </ToolbarButton>
      <ToolbarButton label="Underline" active={state.underline}
                     onClick={() => chain().toggleUnderline().run()}>
        <UnderlineIcon />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-5" />

      <Select
        value={SIZES.some((s) => s.value === state.size) ? state.size : 'normal'}
        onValueChange={(value) =>
          value === 'normal' ? chain().unsetFontSize().run() : chain().setFontSize(value).run()
        }
      >
        <SelectTrigger size="sm" className="h-8 w-28" aria-label="Text size">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {SIZES.map((size) => (
            <SelectItem key={size.value} value={size.value}>
              {size.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Separator orientation="vertical" className="mx-1 h-5" />

      <ToolbarButton label="Bulleted list" active={state.bullets}
                     onClick={() => chain().toggleBulletList().run()}>
        <List />
      </ToolbarButton>
      <ToolbarButton label="Numbered list" active={state.numbers}
                     onClick={() => chain().toggleOrderedList().run()}>
        <ListOrdered />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-5" />

      <ToolbarButton label="Undo" disabled={!state.canUndo} onClick={() => chain().undo().run()}>
        <Undo2 />
      </ToolbarButton>
      <ToolbarButton label="Redo" disabled={!state.canRedo} onClick={() => chain().redo().run()}>
        <Redo2 />
      </ToolbarButton>
    </div>
  )
}

/**
 * `initialHtml` seeds the editor once; `onChange(html)` fires on every
 * edit. Uncontrolled after mount on purpose - re-seeding a ProseMirror
 * document on each keystroke would throw away the cursor.
 */
export default function RichEditor({ initialHtml, onChange, label = 'Body', className }) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: false,
        blockquote: false,
        code: false,
        codeBlock: false,
        horizontalRule: false,
        strike: false,
        link: false,
      }),
      TextStyle,
      FontSize,
    ],
    content: initialHtml || '<p></p>',
    editorProps: {
      attributes: {
        'aria-label': label,
        'aria-multiline': 'true',
        role: 'textbox',
        class: cn(
          'min-h-80 px-3 py-2 text-[15px] leading-relaxed outline-none',
          '[&_p]:my-3 [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-6',
          '[&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-6 [&_li_p]:my-0',
        ),
      },
    },
    onUpdate: ({ editor: e }) => onChange?.(e.getHTML()),
  })

  if (!editor) return null
  return (
    <div
      className={cn(
        'focus-within:ring-ring/50 focus-within:border-ring rounded-md border',
        'overflow-hidden focus-within:ring-[3px]',
        className,
      )}
    >
      <Toolbar editor={editor} />
      <EditorContent editor={editor} />
    </div>
  )
}
