/**
 * Shared chrome for tool-pane titles (map / visualizer / raw / trace / locate).
 *
 * These panes are destinations of the bottom bar on phones, reached the same way
 * as the conversation list, Tools and Settings — so they carry the same title
 * block those do: opaque, clear of the status-bar strip, and at the size a screen
 * title is read at. Desktop keeps the dense rule-under-a-line version, where the
 * pane sits beside a rail that already says where you are.
 */
export const TOOL_PANE_HEADER_CLASS =
  'shrink-0 bg-background px-4 pb-2 pt-8 text-2xl font-semibold tracking-tight ' +
  'md:border-b md:border-border md:py-2.5 md:text-base md:tracking-normal';
