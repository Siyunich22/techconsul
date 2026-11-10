import { expect, test } from "@playwright/test";

import { gotoReady } from "./helpers";

// Оценка рисков требует прогона пунктов (платный ИИ) — здесь вкладки, предусловия и блокировка выпуска.
test("риски и матрица покрытия: без оценки — запрет запуска и выпуска", async ({ page }) => {
  const n = `${Date.now()}`.slice(-12);
  await gotoReady(page, "/register");
  await page.getByLabel("Наименование").fill("ТОО «E2E Риски»");
  await page.getByLabel("БИН").fill(n);
  await page.getByLabel("ФИО").fill("Тестов Т.Т.");
  await page.getByLabel("Email").fill(`e2e-risks-${n}@example.kz`);
  await page.getByLabel("Пароль").fill("e2e-secret-1");
  await page.getByRole("button", { name: "Зарегистрировать" }).click();
  await expect(page).toHaveURL(/\/portfolio$/, { timeout: 20_000 });

  const created = await page.request.post("/api/v1/projects", {
    data: { name: "Завод (e2e риски)", customer_name: "ТОО «Агро»" },
  });
  const { id } = await created.json();

  await gotoReady(page, `/projects/${id}?tab=risks`);
  const risks = page.getByTestId("risks");
  await expect(risks).toContainText("15 обязательных рисков");
  await expect(risks.getByTestId("risk-T1")).toContainText("Риск не оценён");
  await risks.getByRole("button", { name: "Оценить риски и сформировать выводы" }).click();
  await page.getByTestId("risks-confirm").getByRole("button", { name: "Запустить" }).click();
  await expect(risks).toContainText("Оценка ещё не запускалась");

  await gotoReady(page, `/projects/${id}?tab=coverage`);
  const coverage = page.getByTestId("coverage");
  await expect(coverage.getByTestId("approved-count")).toContainText("Утверждено экспертами: 0 из");
  await expect(coverage.getByTestId("row-3.4.1")).toContainText("не сформирован");
  await expect(coverage.getByTestId("row-T1")).toBeVisible();
  await expect(coverage.getByTestId("issues")).toContainText("Пункт 3.4.1 не сформирован");
  await expect(coverage.getByRole("button", { name: "Выпустить итоговую версию" })).toBeDisabled();
});
