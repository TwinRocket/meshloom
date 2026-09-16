import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { ToolPaneHeader } from '../components/ToolPaneHeader';
import { RAIL_ITEMS } from '../components/navDestinations';
import i18n from '../i18n';

/**
 * Every tool pane is a full screen on a phone, so the only way out of one is the
 * control in its header. A pane that draws its own title has no way out, and
 * nothing said so until someone was stuck in it.
 */

describe('ToolPaneHeader', () => {
  it('offers the way back when it is given one', () => {
    const onBack = vi.fn();
    render(<ToolPaneHeader title="Trace" onBack={onBack} />);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('shell.backToTools') }));
    expect(onBack).toHaveBeenCalled();
  });

  it('draws no control when the pane is a destination of its own', () => {
    render(<ToolPaneHeader title="Carte" />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it("keeps the pane's own actions beside the title", () => {
    render(<ToolPaneHeader title="Flux" actions={<button type="button">Analyser</button>} />);
    expect(screen.getByRole('button', { name: 'Analyser' })).toBeInTheDocument();
  });
});

describe('tool panes', () => {
  it('none of them writes its own title bar', () => {
    // SearchView kept an inlined copy of the old dense header — the same utility
    // string, written out rather than imported, which is why converting the others
    // missed it. The pane had no back control at all as a result.
    const dir = join(__dirname, '..', 'components');
    const panes = readdirSync(dir).filter((f) => /^(SearchView|.*Pane|.*View)\.tsx$/.test(f));
    const offenders = panes.filter((file) => {
      const source = readFileSync(join(dir, file), 'utf8');
      return /py-2\.5 border-b border-border font-semibold/.test(source);
    });
    expect(offenders).toEqual([]);
  });

  it('every tool on the rail is reachable and leaves a way back', () => {
    // The rail is the only route to these on desktop, and Tools the only route on a
    // phone; either way each one opens full screen.
    for (const item of RAIL_ITEMS.filter((i) => !i.permanent && !i.overlay)) {
      expect(item.conversation).toBeDefined();
    }
    for (const item of RAIL_ITEMS.filter((i) => i.overlay)) {
      expect(item.conversation).toBeUndefined();
    }
  });
});
