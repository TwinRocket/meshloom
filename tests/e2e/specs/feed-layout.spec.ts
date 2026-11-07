import { test, expect, type Page } from '@playwright/test';
import { seedChannelMessages } from '../helpers/seed';

/**
 * Layout invariants for the Feed (`#feed`), modelled on chat-layout.spec.ts.
 *
 * Out of the CI gate like every spec in tests/e2e (it needs a radio; see docs/dev/feed.md,
 * "Device checklist"). The same invariants were probed without a radio against a seeded
 * database with Playwright device emulation. They assert behaviour, not pixels: how many
 * elements scroll vertically, whether the page itself scrolls, whether the floating
 * Follow button is reachable above the bottom bar.
 */

const CHANNEL_NAME = '#feed-layout-e2e';
const PHONE = { width: 360, height: 740 };

async function readLayout(page: Page) {
  return page.evaluate(() => {
    const scrollers = [...document.querySelectorAll<HTMLElement>('div')].filter(
      (el) =>
        el.clientHeight > 0 &&
        el.scrollHeight - el.clientHeight > 40 &&
        /auto|scroll/.test(getComputedStyle(el).overflowY)
    );
    const doc = document.scrollingElement as HTMLElement;
    const follow = document.querySelector<HTMLElement>('[data-above-bottom-nav]');
    const nav = document.querySelector<HTMLElement>('nav[data-bottom-nav]');
    const followBox = follow?.getBoundingClientRect() ?? null;
    const navBox = nav?.getBoundingClientRect() ?? null;
    return {
      // Only the list may scroll vertically: no nested scroller, no scrolling shell.
      scrollerCount: scrollers.length,
      listHoldsRows: scrollers.some((el) => el.querySelector('[data-index]')),
      documentCanScroll: doc.scrollHeight - doc.clientHeight > 1,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      followClearOfBar:
        !followBox || !navBox || navBox.height < 10 ? null : followBox.bottom <= navBox.top + 1,
      mountedRows: document.querySelectorAll('[role="listitem"]').length,
    };
  });
}

async function openFeed(page: Page) {
  await page.goto('/#feed');
  await expect(page.getByRole('list').first()).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(1_500);
}

test.describe('Feed layout', () => {
  test.beforeEach(() => {
    seedChannelMessages({
      channelName: CHANNEL_NAME,
      count: 400,
      startTimestamp: Math.floor(Date.now() / 1000) - 420,
      outgoingEvery: 9,
    });
  });

  test('exactly one vertical scroller on a phone', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await openFeed(page);

    const layout = await readLayout(page);
    expect(layout.scrollerCount).toBe(1);
    expect(layout.listHoldsRows).toBe(true);
    expect(layout.documentCanScroll).toBe(false);
    expect(layout.horizontalOverflow).toBe(false);
    // The DOM cap holds whatever the store holds.
    expect(layout.mountedRows).toBeLessThanOrEqual(300);
  });

  test('exactly one vertical scroller on a desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openFeed(page);

    const layout = await readLayout(page);
    expect(layout.scrollerCount).toBe(1);
    expect(layout.documentCanScroll).toBe(false);
    expect(layout.horizontalOverflow).toBe(false);
  });

  test('scrolling up moves the list only, and Follow clears the bottom bar', async ({ page }) => {
    await page.setViewportSize(PHONE);
    await openFeed(page);

    await page.evaluate(() => {
      const scroller = [...document.querySelectorAll<HTMLElement>('div')].find(
        (el) =>
          el.scrollHeight - el.clientHeight > 40 &&
          /auto|scroll/.test(getComputedStyle(el).overflowY) &&
          el.querySelector('[data-index]')
      );
      if (scroller) scroller.scrollTop -= 1_500;
    });
    await page.waitForTimeout(500);

    const layout = await readLayout(page);
    expect(layout.scrollerCount).toBe(1);
    expect(layout.documentCanScroll).toBe(false);
    expect(layout.followClearOfBar).toBe(true);
  });
});
