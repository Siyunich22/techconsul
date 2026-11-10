"use client";

import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import {
  Bold,
  Heading3,
  Italic,
  List,
  ListOrdered,
  Redo2,
  Undo2,
} from "lucide-react";
import { Markdown } from "tiptap-markdown";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type MdStorage = { markdown: { getMarkdown: () => string } };

/** Сериализатор Markdown экранирует квадратные скобки: «\[S12\]» → «[S12]» (ссылки на источники). */
export const unescapeRefs = (md: string) =>
  md.replace(/\\\[(S\d+)\\\]/g, "[$1]");

/** Редактор аргументации пункта (TipTap): хранение — Markdown, ссылки [S12] — обычный текст. */
export function MarkdownEditor({
  value,
  onChange,
  label,
}: {
  value: string;
  onChange: (markdown: string) => void;
  label: string;
}) {
  const editor = useEditor({
    immediatelyRender: false, // Next.js: без рендера на сервере
    extensions: [
      StarterKit.configure({
        heading: { levels: [3] },
        code: false,
        codeBlock: false,
      }),
      Markdown.configure({ html: false, transformPastedText: true }),
    ],
    content: value,
    editorProps: {
      attributes: {
        "aria-label": label,
        class:
          "prose-sm min-h-48 max-w-none px-3 py-2 focus:outline-none [&_h3]:font-semibold [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5 [&_p]:my-1",
      },
    },
    onUpdate: ({ editor }) =>
      onChange(
        unescapeRefs(
          (editor.storage as unknown as MdStorage).markdown.getMarkdown(),
        ),
      ),
  });

  const tool = (
    title: string,
    icon: React.ReactNode,
    run: () => void,
    active = false,
  ) => (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      title={title}
      className={cn("h-7 px-2", active && "bg-muted")}
      onClick={run}
      disabled={!editor}
    >
      {icon}
    </Button>
  );

  return (
    <div className="rounded-md border" data-testid="md-editor">
      <div className="flex flex-wrap gap-0.5 border-b p-1">
        {tool(
          "Полужирный",
          <Bold />,
          () => editor?.chain().focus().toggleBold().run(),
          editor?.isActive("bold"),
        )}
        {tool(
          "Курсив",
          <Italic />,
          () => editor?.chain().focus().toggleItalic().run(),
          editor?.isActive("italic"),
        )}
        {tool(
          "Подзаголовок",
          <Heading3 />,
          () => editor?.chain().focus().toggleHeading({ level: 3 }).run(),
          editor?.isActive("heading"),
        )}
        {tool(
          "Список",
          <List />,
          () => editor?.chain().focus().toggleBulletList().run(),
          editor?.isActive("bulletList"),
        )}
        {tool(
          "Нумерованный список",
          <ListOrdered />,
          () => editor?.chain().focus().toggleOrderedList().run(),
          editor?.isActive("orderedList"),
        )}
        {tool("Отменить", <Undo2 />, () =>
          editor?.chain().focus().undo().run(),
        )}
        {tool("Повторить", <Redo2 />, () =>
          editor?.chain().focus().redo().run(),
        )}
      </div>
      <EditorContent editor={editor} />
    </div>
  );
}
