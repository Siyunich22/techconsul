import { expect, test } from "@playwright/test";

import { gotoReady } from "./helpers";

// Живой стек: без ключа Anthropic извлечение заканчивается «Ошибка» с причиной, с ключом — «Черновик».
// Проверяем сценарий интерфейса: запуск → прогресс → итоговое состояние; содержание паспорта — в API-тестах.
test("паспорт проекта: запуск извлечения из вкладки и итоговый статус", async ({ page }) => {
  test.setTimeout(300_000);
  const n = `${Date.now()}`.slice(-12);
  await gotoReady(page, "/register");
  await page.getByLabel("Наименование").fill("ТОО «E2E Паспорт»");
  await page.getByLabel("БИН").fill(n);
  await page.getByLabel("ФИО").fill("Тестов Т.Т.");
  await page.getByLabel("Email").fill(`e2e-passport-${n}@example.kz`);
  await page.getByLabel("Пароль").fill("e2e-secret-1");
  await page.getByRole("button", { name: "Зарегистрировать" }).click();
  await expect(page).toHaveURL(/\/portfolio$/, { timeout: 20_000 });

  const created = await page.request.post("/api/v1/projects", {
    data: { name: "Завод (e2e паспорт)", customer_name: "ТОО «Агро»" },
  });
  const { id } = await created.json();

  // без документов запуск невозможен
  await gotoReady(page, `/projects/${id}?tab=passport`);
  await expect(page.getByTestId("passport-status")).toHaveText("Не извлечён");
  await page.getByRole("button", { name: "Извлечь паспорт" }).click();
  await expect(page.getByText("Нет обработанных документов")).toBeVisible();

  // документ → проиндексирован
  await gotoReady(page, `/projects/${id}?tab=documents`);
  await page.getByTestId("file-input").setInputFiles({
    name: "Бизнес-план.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Бизнес-план. Производственная мощность завода — 300 тонн в сутки."),
  });
  await expect(page.getByTestId("doc-status")).toHaveText("Проиндексирован", { timeout: 120_000 });

  await page.getByRole("button", { name: "Паспорт проекта" }).click();
  await page.getByRole("button", { name: "Извлечь паспорт" }).click();
  await expect(page.getByTestId("passport-status")).toHaveText(/Черновик|Ошибка/, { timeout: 240_000 });
  if ((await page.getByTestId("passport-status").innerText()) === "Ошибка") {
    await expect(page.getByText(/Не удалось извлечь паспорт: .+/)).toBeVisible();
  } else {
    await expect(page.getByTestId("passport-section-technology")).toBeVisible();
  }
});
