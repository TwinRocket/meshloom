import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { Dialog, DialogContent, DialogTitle } from '../components/ui/dialog';

/**
 * A dialog is centred, so the height it does not use is split equally above and
 * below it. Without a ceiling that knows about the device's insets, a tall one
 * reaches into the strip iOS draws its status bar over — which is where the packet
 * analyser's title and close control ended up, blurred and unreachable.
 */

describe('dialog height', () => {
  it('every dialog is bounded by the insets, not just by padding', () => {
    render(
      <Dialog open>
        <DialogContent>
          <DialogTitle>Analyser le paquet</DialogTitle>
        </DialogContent>
      </Dialog>
    );
    expect(screen.getByRole('dialog').className).toContain('dialog-safe-height');
  });

  it('no dialog writes its own ceiling that forgets them', () => {
    // Three modals carried `max-h-[calc(100dvh-2rem)]`, which subtracts padding and
    // nothing else, and overrode the primitive because it is written later.
    const root = join(__dirname, '..', 'components');
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (entry.name.endsWith('.tsx')) files.push(full);
      }
    };
    walk(root);

    const offenders = files.filter((file) =>
      /max-h-\[calc\(100dvh-2rem\)\]/.test(readFileSync(file, 'utf8'))
    );
    expect(offenders).toEqual([]);
  });
});
