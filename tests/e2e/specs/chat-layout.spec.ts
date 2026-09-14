import { test, expect, type Page } from '@playwright/test';
import { seedChannelMessages } from '../helpers/seed';

/**
 * Layout invariants for the conversation surface.
 *
 * These assert behaviour, not pixels: which element owns vertical scrolling, and
 * whether the composer is somewhere the user can reach. jsdom cannot see any of it —
 * it has no layout engine — so this is the only place the guarantees are observable.
 */

const CHANNEL_NAME = '#chat-layout-e2e';
const OTHER_CHANNEL_NAME = '#chat-layout-e2e-b';
const MOBILE = { width: 390, height: 844 };

/** Geometry of the pieces the invariants are about, read from the live document. */
async function readLayout(page: Page) {
  return page.evaluate(() => {
    const textarea = document.querySelector('textarea');
    let composer: HTMLElement | null = textarea?.parentElement ?? null;
    while (composer && !/border-t/.test(String(composer.className))) {
      composer = composer.parentElement;
    }
    const composerBox = composer?.getBoundingClientRect() ?? null;

    const scrollers = [...document.querySelectorAll<HTMLElement>('div')].filter(
      (el) =>
        el.scrollHeight - el.clientHeight > 40 && /auto|scroll/.test(getComputedStyle(el).overflowY)
    );
    const messageScroller = scrollers.find((el) => el.querySelector('[data-index]')) ?? null;
    const doc = document.scrollingElement as HTMLElement;

    return {
      composer: composerBox
        ? {
            top: Math.round(composerBox.top),
            bottom: Math.round(composerBox.bottom),
            height: Math.round(composerBox.height),
          }
        : null,
      composerInViewport: !!(
        composerBox &&
        composerBox.height > 10 &&
        composerBox.top >= 0 &&
        composerBox.bottom <= window.innerHeight + 1
      ),
      messageScrollerFound: !!messageScroller,
      messageScrollTop: messageScroller ? Math.round(messageScroller.scrollTop) : null,
      messageScrollHeight: messageScroller?.scrollHeight ?? null,
      messageClientHeight: messageScroller?.clientHeight ?? null,
      documentCanScroll: doc.scrollHeight - doc.clientHeight > 1,
      documentScrollTop: Math.round(doc.scrollTop),
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      textareaHeight: textarea ? Math.round(textarea.getBoundingClientRect().height) : null,
    };
  });
}

async function scrollMessages(page: Page, delta: number) {
  await page.evaluate((dy) => {
    const scroller = [...document.querySelectorAll<HTMLElement>('div')].find(
      (el) =>
        el.scrollHeight - el.clientHeight > 40 &&
        /auto|scroll/.test(getComputedStyle(el).overflowY) &&
        el.querySelector('[data-index]')
    );
    if (scroller) scroller.scrollTop += dy;
  }, delta);
}

async function openSeededChannel(page: Page, key: string, name: string) {
  await page.goto(`/#channel/${key}/${name.replace('#', '')}`);
  await expect(page.getByPlaceholder(/message/i)).toBeVisible({ timeout: 15_000 });
  // Let the deferred bottom-pin settle before measuring anything.
  await page.waitForTimeout(1_500);
}

test.describe('Conversation layout', () => {
  test('the message list owns the scroll and the composer stays reachable', async ({ page }) => {
    const seeded = seedChannelMessages({
      channelName: CHANNEL_NAME,
      count: 400,
      startTimestamp: Math.floor(Date.now() / 1000) - 420,
      outgoingEvery: 9,
    });

    await page.setViewportSize(MOBILE);
    await openSeededChannel(page, seeded.key, CHANNEL_NAME);

    const onOpen = await readLayout(page);
    expect(onOpen.messageScrollerFound).toBe(true);
    expect(onOpen.composerInViewport).toBe(true);
    // The shell must not be a second scroll container competing with the list.
    expect(onOpen.documentCanScroll).toBe(false);
    expect(onOpen.horizontalOverflow).toBe(false);

    // Scrolling history must move the history, and nothing else.
    await scrollMessages(page, -1_200);
    await page.waitForTimeout(400);
    const scrolled = await readLayout(page);
    expect(scrolled.messageScrollTop).toBeLessThan(onOpen.messageScrollTop ?? 0);
    expect(scrolled.composerInViewport).toBe(true);
    expect(scrolled.documentScrollTop).toBe(0);
    expect(scrolled.documentCanScroll).toBe(false);
  });

  test('loading older history keeps the composer in place', async ({ page }) => {
    const seeded = seedChannelMessages({
      channelName: CHANNEL_NAME,
      count: 400,
      startTimestamp: Math.floor(Date.now() / 1000) - 420,
    });

    await page.setViewportSize(MOBILE);
    await openSeededChannel(page, seeded.key, CHANNEL_NAME);

    // Walk to the top repeatedly: each arrival at the top fetches an older page.
    for (let i = 0; i < 6; i++) {
      await page.evaluate(() => {
        const scroller = [...document.querySelectorAll<HTMLElement>('div')].find(
          (el) =>
            el.scrollHeight - el.clientHeight > 40 &&
            /auto|scroll/.test(getComputedStyle(el).overflowY) &&
            el.querySelector('[data-index]')
        );
        if (scroller) scroller.scrollTop = 0;
      });
      await page.waitForTimeout(700);
      const during = await readLayout(page);
      expect(during.composerInViewport).toBe(true);
      expect(during.documentCanScroll).toBe(false);
    }
  });

  test('a long draft does not push the history or the send controls off screen', async ({
    page,
  }) => {
    const seeded = seedChannelMessages({
      channelName: CHANNEL_NAME,
      count: 60,
      startTimestamp: Math.floor(Date.now() / 1000) - 80,
    });

    await page.setViewportSize(MOBILE);
    await openSeededChannel(page, seeded.key, CHANNEL_NAME);

    const before = await readLayout(page);
    await page.locator('textarea').first().fill(
      Array.from({ length: 40 }, (_, i) => `draft line ${i + 1}`).join('\n')
    );
    await page.waitForTimeout(500);
    const after = await readLayout(page);

    expect(after.composerInViewport).toBe(true);
    expect(after.documentCanScroll).toBe(false);
    // The textarea grows, but to a bounded height — the history keeps a usable share.
    expect(after.textareaHeight).toBeGreaterThan(before.textareaHeight ?? 0);
    expect(after.composer!.height).toBeLessThan(MOBILE.height * 0.5);
    expect(after.messageClientHeight).toBeGreaterThan(120);
  });

  test('switching conversations leaves the composer reachable', async ({ page }) => {
    const first = seedChannelMessages({
      channelName: CHANNEL_NAME,
      count: 200,
      startTimestamp: Math.floor(Date.now() / 1000) - 220,
    });
    const second = seedChannelMessages({
      channelName: OTHER_CHANNEL_NAME,
      count: 200,
      startTimestamp: Math.floor(Date.now() / 1000) - 220,
    });

    await page.setViewportSize(MOBILE);
    await openSeededChannel(page, first.key, CHANNEL_NAME);

    for (const target of [second, first, second, first]) {
      await page.evaluate((hash) => {
        window.location.hash = hash;
      }, `#channel/${target.key}`);
      await page.waitForTimeout(600);
    }
    await page.waitForTimeout(1_200);

    const after = await readLayout(page);
    expect(after.composerInViewport).toBe(true);
    expect(after.documentCanScroll).toBe(false);
    expect(after.horizontalOverflow).toBe(false);
  });

  test('the bottom bar stands down inside a conversation and never covers the composer', async ({
    page,
  }) => {
    const seeded = seedChannelMessages({
      channelName: CHANNEL_NAME,
      count: 120,
      startTimestamp: Math.floor(Date.now() / 1000) - 140,
    });

    await page.setViewportSize(MOBILE);

    // A tool view: the bar is the primary navigation there.
    await page.goto('/#raw');
    await page.waitForTimeout(1_500);
    const onTool = await page.evaluate(() => {
      const bar = document.querySelector('nav.fixed[aria-label]');
      const box = bar?.getBoundingClientRect();
      return {
        present: !!box && box.height > 10,
        insideViewport: !!box && box.bottom <= window.innerHeight + 1 && box.top >= 0,
      };
    });
    expect(onTool.present).toBe(true);
    expect(onTool.insideViewport).toBe(true);

    // A conversation: the composer owns the bottom instead.
    await openSeededChannel(page, seeded.key, CHANNEL_NAME);
    const inChat = await page.evaluate(() => {
      const bar = document.querySelector('nav.fixed[aria-label]');
      const textarea = document.querySelector('textarea');
      let composer: HTMLElement | null = textarea?.parentElement ?? null;
      while (composer && !/border-t/.test(String(composer.className))) {
        composer = composer.parentElement;
      }
      const composerBox = composer?.getBoundingClientRect();
      const barBox = bar?.getBoundingClientRect();
      return {
        barPresent: !!barBox && barBox.height > 10,
        composerInViewport: !!(
          composerBox &&
          composerBox.top >= 0 &&
          composerBox.bottom <= window.innerHeight + 1
        ),
        overlap: !!(
          barBox &&
          composerBox &&
          composerBox.bottom > barBox.top &&
          composerBox.top < barBox.bottom
        ),
      };
    });
    expect(inChat.barPresent).toBe(false);
    expect(inChat.composerInViewport).toBe(true);
    expect(inChat.overlap).toBe(false);
  });

  test('the composer survives viewport changes at every size class', async ({ page }) => {
    const seeded = seedChannelMessages({
      channelName: CHANNEL_NAME,
      count: 300,
      startTimestamp: Math.floor(Date.now() / 1000) - 320,
    });

    await page.setViewportSize({ width: 1440, height: 900 });
    await openSeededChannel(page, seeded.key, CHANNEL_NAME);

    const sizes = [
      { width: 1920, height: 1080 },
      { width: 1440, height: 900 },
      { width: 1280, height: 800 },
      { width: 768, height: 1024 },
      { width: 430, height: 932 },
      { width: 390, height: 844 },
      { width: 375, height: 667 },
      // A viewport the height of a phone with the keyboard up.
      { width: 390, height: 420 },
      { width: 390, height: 844 },
    ];

    for (const size of sizes) {
      await page.setViewportSize(size);
      await page.waitForTimeout(450);
      const layout = await readLayout(page);
      expect(
        layout.composerInViewport,
        `composer outside the viewport at ${size.width}x${size.height}`
      ).toBe(true);
      expect(
        layout.horizontalOverflow,
        `horizontal overflow at ${size.width}x${size.height}`
      ).toBe(false);
      expect(
        layout.documentCanScroll,
        `the shell scrolls at ${size.width}x${size.height}`
      ).toBe(false);
    }
  });
});
