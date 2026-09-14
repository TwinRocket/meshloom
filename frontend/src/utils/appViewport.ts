/**
 * Keeps the app shell as tall as the part of the screen the user can actually see.
 *
 * The shell is sized from `100dvh`, and `dvh` tracks the browser's own chrome — the
 * collapsing address bar — but not the virtual keyboard. On a phone the keyboard
 * shrinks the *visual* viewport while the layout viewport keeps its full height, so
 * a composer laid out at the bottom of a `100dvh` column is laid out underneath the
 * keyboard. It is on screen as far as CSS is concerned and unreachable as far as the
 * user is concerned, which is how someone ends up unable to send a message until they
 * pan the page back by hand.
 *
 * `interactive-widget=resizes-content` in the viewport meta fixes this in the browser
 * itself, by shrinking the layout viewport instead of overlaying it. Where it is
 * honoured this module measures no meaningful difference and writes nothing. It exists
 * for the browsers that ignore it — iOS Safari among them — where `visualViewport` is
 * the only thing that reports the keyboard at all.
 *
 * Deliberately narrow: it writes one custom property, only while the visible area is
 * meaningfully shorter than the layout viewport, and removes it as soon as that stops
 * being true. Everything else about the layout stays in CSS.
 *
 * Do not widen it to "always measure when installed". That was tried, to close a band
 * at the bottom of the screen, and it took the bottom bar off screen with it: iOS
 * anchors `position: fixed` to the layout viewport, so making the document a
 * different height from it puts `bottom: 0` somewhere the user cannot see. The band
 * belongs to the page's own insets, not to the document height.
 */

/** Height of the visible area, when it differs enough from the layout viewport to matter. */
const APP_HEIGHT_VAR = '--app-height';

/**
 * Below this the difference is browser chrome settling or a rounding artefact, not a
 * keyboard. Reacting to those would fight `dvh` and make the shell twitch on scroll.
 */
const KEYBOARD_MIN_DELTA_PX = 120;

export function initAppViewport(): () => void {
  const vv = typeof window !== 'undefined' ? window.visualViewport : undefined;
  if (!vv) return () => {};

  const root = document.documentElement;

  /**
   * A keyboard needs something to type into. Without this gate the height was driven
   * by the measurement alone, and an installed app reports a much shorter visual
   * viewport while it is still opening — which looked exactly like a keyboard, got
   * written down, and was never revisited because no further event arrived. The
   * result was a shell a third shorter than the screen for the whole session, with
   * the bar still anchored to the viewport and a void between the two.
   */
  const keyboardPlausible = () => {
    const el = document.activeElement;
    if (!el) return false;
    const tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || (el as HTMLElement).isContentEditable === true;
  };

  const apply = () => {
    const layoutHeight = window.innerHeight;
    const visibleHeight = vv.height;
    if (keyboardPlausible() && layoutHeight - visibleHeight >= KEYBOARD_MIN_DELTA_PX) {
      root.style.setProperty(APP_HEIGHT_VAR, `${Math.round(visibleHeight)}px`);
    } else {
      root.style.removeProperty(APP_HEIGHT_VAR);
    }
  };

  apply();
  vv.addEventListener('resize', apply);
  // The visual viewport also pans: iOS scrolls the focused field into view by moving
  // it rather than resizing, and the offset is what leaves the layout looking shifted.
  vv.addEventListener('scroll', apply);
  // Focus changes are the other half: the keyboard closing is a blur, not a resize.
  document.addEventListener('focusin', apply, true);
  document.addEventListener('focusout', apply, true);
  return () => {
    vv.removeEventListener('resize', apply);
    vv.removeEventListener('scroll', apply);
    document.removeEventListener('focusin', apply, true);
    document.removeEventListener('focusout', apply, true);
    root.style.removeProperty(APP_HEIGHT_VAR);
  };
}
