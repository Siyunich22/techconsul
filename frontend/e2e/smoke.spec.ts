import { expect, test } from "@playwright/test";

test("корень редиректит на страницу логина", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Вход" })).toBeVisible();
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByLabel("Пароль")).toBeVisible();
});

test("API доступен через прокси Next.js", async ({ request }) => {
  const resp = await request.get("/api/v1/health");
  expect(resp.ok()).toBeTruthy();
  expect((await resp.json()).status).toBe("ok");
});
