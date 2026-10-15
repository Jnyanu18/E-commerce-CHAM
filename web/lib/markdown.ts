/**
 * Minimal safe markdown parser for AI briefs. Produces a plain AST; the renderer maps it to React
 * elements, so no raw HTML is ever injected (angle brackets are just text).
 * Supported: #..#### headings, -/* bullets, "1." numbered items, paragraphs, **bold**, `code`, [cN] citations.
 */
export type Inline =
  | { t: "text"; v: string }
  | { t: "bold"; v: string }
  | { t: "code"; v: string }
  | { t: "cite"; id: string };

export type Block =
  | { t: "heading"; level: 1 | 2 | 3 | 4; inline: Inline[] }
  | { t: "paragraph"; inline: Inline[] }
  | { t: "ul"; items: Inline[][] }
  | { t: "ol"; items: Inline[][] };

const INLINE = /\*\*([^*]+)\*\*|`([^`]+)`|\[([A-Za-z][\w-]*\d+)\]/g;

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    const i = m.index ?? 0;
    if (i > last) out.push({ t: "text", v: text.slice(last, i) });
    if (m[1] !== undefined) out.push({ t: "bold", v: m[1] });
    else if (m[2] !== undefined) out.push({ t: "code", v: m[2] });
    else out.push({ t: "cite", id: m[3] });
    last = i + m[0].length;
  }
  if (last < text.length) out.push({ t: "text", v: text.slice(last) });
  return out;
}

export function parseMarkdown(md: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: { kind: "ul" | "ol"; items: Inline[][] } | null = null;
  const flushPara = () => {
    if (para.length) blocks.push({ t: "paragraph", inline: parseInline(para.join(" ")) });
    para = [];
  };
  const flushList = () => {
    if (list) blocks.push({ t: list.kind, items: list.items });
    list = null;
  };
  for (const raw of md.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    const ul = /^\s*[-*]\s+(.*)$/.exec(line);
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (!line.trim()) {
      flushPara();
      flushList();
    } else if (h) {
      flushPara();
      flushList();
      blocks.push({ t: "heading", level: h[1].length as 1 | 2 | 3 | 4, inline: parseInline(h[2]) });
    } else if (ul || ol) {
      flushPara();
      const kind = ul ? "ul" : "ol";
      if (list && list.kind !== kind) flushList();
      list ??= { kind, items: [] };
      list.items.push(parseInline((ul ?? ol)![1]));
    } else if (list && /^\s+\S/.test(raw)) {
      // continuation line of the previous list item
      list.items[list.items.length - 1].push(...parseInline(" " + line.trim()));
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  flushPara();
  flushList();
  return blocks;
}

/** All citation ids referenced anywhere in the markdown, in first-seen order. */
export function citationIds(md: string): string[] {
  const seen: string[] = [];
  for (const m of md.matchAll(/\[([A-Za-z][\w-]*\d+)\]/g)) if (!seen.includes(m[1])) seen.push(m[1]);
  return seen;
}
