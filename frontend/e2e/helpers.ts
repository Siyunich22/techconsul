import { expect, type Page } from "@playwright/test";

/** Переход на страницу и ожидание гидратации React (иначе ввод в форму может потеряться). */
export async function gotoReady(page: Page, url: string) {
  await page.goto(url);
  await expect(page.locator("body[data-hydrated='true']")).toBeAttached();
}
