import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { gotoReady } from "./helpers";

// Администратор создаётся make seed (admin@techocenka.kz).
const TEMPLATE = readFileSync(path.resolve(__dirname, "../../templates/tech_assessment_bank_v1.yaml"), "utf-8");

test("администратор: шаблон ТЗ — проверка, дерево пунктов, загрузка версии", async ({ page }) => {
  await gotoReady(page, "/login");
  await page.getByLabel("Email").fill("admin@techocenka.kz");
  await page.getByLabel("Пароль").fill("admin-pass-1");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/portfolio$/, { timeout: 20_000 });

  await page.getByRole("link", { name: "Администрирование" }).click();
  await expect(page.getByRole("heading", { name: "Шаблоны ТЗ" })).toBeVisible();
  const v1 = page.locator("div.rounded-lg", { hasText: "tech_assessment_bank_v1" }).first();
  await expect(v1).toContainText("по умолчанию");
  await expect(v1).toContainText("обязательных рисков: 15");
  await expect(v1).toContainText("Предупреждения проверки (5)");

  // дерево пунктов строится из шаблона
  await v1.getByRole("button", { name: "Структура" }).click();
  const tree = page.getByTestId("template-tree");
  await expect(tree).toContainText("Оценка проектного оборудования");
  await tree.getByRole("button", { name: /^3\.4 / }).click();
  await tree.getByRole("button", { name: /3\.4\.1\s*Обоснованность выбора оборудования/ }).click();
  await expect(tree).toContainText("не менее 3-х");
  await expect(tree).toContainText("Коммерческие предложения поставщиков");

  // битый шаблон — понятные ошибки, загрузка недоступна
  const fileInput = page.getByLabel("Файл шаблона (.yaml)");
  const broken = TEMPLATE.replace("code: tech_assessment_bank_v1", "code: e2e_broken").replace(
    "inputs: [тэо_bfs, кп_поставщиков]",
    "inputs: [тэо_bfs, чертежи]",
  );
  await fileInput.setInputFiles({ name: "broken.yaml", mimeType: "application/x-yaml", buffer: Buffer.from(broken) });
  await page.getByRole("button", { name: "Проверить" }).click();
  await expect(page.getByText("Шаблон не прошёл проверку")).toBeVisible();
  await expect(page.getByTestId("check-issues")).toContainText("Категория «чертежи» отсутствует");
  await expect(page.getByRole("button", { name: "Загрузить", exact: true })).toBeDisabled();

  // корректная новая версия загружается, неиспользуемая — удаляется
  const code = `e2e_tpl_${Date.now()}`;
  const v2 = TEMPLATE.replace("code: tech_assessment_bank_v1", `code: ${code}`);
  await fileInput.setInputFiles({ name: "v2.yaml", mimeType: "application/x-yaml", buffer: Buffer.from(v2) });
  await page.getByRole("button", { name: "Проверить" }).click();
  await expect(page.getByText(`Шаблон прошёл проверку: ${code}`)).toBeVisible();
  await page.getByRole("button", { name: "Загрузить", exact: true }).click();
  const card = page.locator("div.rounded-lg", { hasText: code }).first();
  await expect(card).toContainText("проектов: 0");
  page.once("dialog", (d) => d.accept());
  await card.getByRole("button", { name: "Удалить" }).click();
  await expect(page.locator("div.rounded-lg", { hasText: code })).toHaveCount(0);
});
