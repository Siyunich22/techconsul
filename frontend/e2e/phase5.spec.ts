import { expect, test } from "@playwright/test";

import { gotoReady } from "./helpers";

// Прогон оценки на живом стеке тратит деньги на API — здесь только интерфейс запуска и предусловия.
test("оценка: предупреждение о стоимости и запрет запуска без подтверждённого паспорта", async ({ page }) => {
  const n = `${Date.now()}`.slice(-12);
  await gotoReady(page, "/register");
  await page.getByLabel("Наименование").fill("ТОО «E2E Оценка»");
  await page.getByLabel("БИН").fill(n);
  await page.getByLabel("ФИО").fill("Тестов Т.Т.");
  await page.getByLabel("Email").fill(`e2e-assess-${n}@example.kz`);
  await page.getByLabel("Пароль").fill("e2e-secret-1");
  await page.getByRole("button", { name: "Зарегистрировать" }).click();
  await expect(page).toHaveURL(/\/portfolio$/, { timeout: 20_000 });

  const created = await page.request.post("/api/v1/projects", {
    data: { name: "Завод (e2e оценка)", customer_name: "ТОО «Агро»" },
  });
  const { id } = await created.json();

  await gotoReady(page, `/projects/${id}?tab=assessment`);
  await page.getByRole("button", { name: "Запустить оценку" }).click();
  const confirm = page.getByTestId("run-confirm");
  await expect(confirm).toContainText("Пунктов для ИИ: 40, из данных проекта: 3");
  await expect(confirm).toContainText("Ориентировочная стоимость");
  await expect(confirm).toContainText("Сначала подтвердите паспорт проекта");
  await expect(confirm.getByRole("button", { name: "Запустить" })).toBeDisabled();
});
