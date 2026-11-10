import { expect, test } from "@playwright/test";

import { gotoReady } from "./helpers";

// Сборка отчёта требует прогона оценки (платный ИИ) — здесь вкладка и запрет сборки без оценки.
test("результат: вкладка отчёта, без оценки черновик не собирается", async ({ page }) => {
  const n = `${Date.now()}`.slice(-12);
  await gotoReady(page, "/register");
  await page.getByLabel("Наименование").fill("ТОО «E2E Отчёт»");
  await page.getByLabel("БИН").fill(n);
  await page.getByLabel("ФИО").fill("Тестов Т.Т.");
  await page.getByLabel("Email").fill(`e2e-report-${n}@example.kz`);
  await page.getByLabel("Пароль").fill("e2e-secret-1");
  await page.getByRole("button", { name: "Зарегистрировать" }).click();
  await expect(page).toHaveURL(/\/portfolio$/, { timeout: 20_000 });

  const created = await page.request.post("/api/v1/projects", {
    data: { name: "Завод (e2e отчёт)", customer_name: "ТОО «Агро»" },
  });
  const { id } = await created.json();

  await gotoReady(page, `/projects/${id}?tab=result`);
  const result = page.getByTestId("result");
  await expect(result).toContainText("Отчёт ещё не формировался");
  await result.getByRole("button", { name: "Сформировать черновик" }).click();
  await expect(result).toContainText("Оценка ещё не запускалась");
});
