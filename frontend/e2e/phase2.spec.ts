import { existsSync, readdirSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { gotoReady } from "./helpers";

// Файлы sample_project генерирует make seed (backend/tests/fixtures/sample_project/files).
const SAMPLE_DIR = path.resolve(__dirname, "../../backend/tests/fixtures/sample_project/files");

test.describe("фаза 2: загрузка и ingest", () => {
  test.skip(!existsSync(SAMPLE_DIR), "нет файлов sample_project — выполните make seed");
  test.setTimeout(600_000); // OCR, LibreOffice и эмбеддинги в живом стеке

  test("sample_project: загрузка → все файлы проиндексированы → полнота пакета → предпросмотр", async ({ page }) => {
    const n = `${Date.now()}`.slice(-12);
    await gotoReady(page, "/register");
    await page.getByLabel("Наименование").fill("ТОО «E2E Ingest»");
    await page.getByLabel("БИН").fill(n);
    await page.getByLabel("ФИО").fill("Тестов Т.Т.");
    await page.getByLabel("Email").fill(`e2e-ingest-${n}@example.kz`);
    await page.getByLabel("Пароль").fill("e2e-secret-1");
    await page.getByRole("button", { name: "Зарегистрировать" }).click();
    await expect(page).toHaveURL(/\/portfolio$/, { timeout: 20_000 });

    const created = await page.request.post("/api/v1/projects", {
      data: { name: "Мукомольный завод (e2e ingest)", customer_name: "ТОО «Агро»" },
    });
    const { id } = await created.json();
    await gotoReady(page, `/projects/${id}?tab=documents`);
    await expect(page.getByText("Перетащите файлы, папки или архивы сюда")).toBeVisible();

    const files = readdirSync(SAMPLE_DIR).map((f) => path.join(SAMPLE_DIR, f));
    expect(files).toHaveLength(7);
    await page.getByTestId("file-input").setInputFiles(files);

    const statuses = page.getByTestId("doc-status");
    await expect(statuses).toHaveCount(7, { timeout: 60_000 });
    await expect(async () => {
      const texts = await statuses.allInnerTexts();
      expect(texts.filter((s) => s === "Проиндексирован")).toHaveLength(7);
    }).toPass({ timeout: 540_000, intervals: [3_000] });

    // категории определены автоматически
    const row = (name: RegExp) => page.getByRole("row", { name });
    await expect(row(/ТЭО_мукомольный/).getByLabel("Категория")).toHaveValue("тэо_bfs");
    await expect(row(/ТУ_электроснабжение/).getByLabel("Категория")).toHaveValue("техусловия");
    await expect(row(/ТУ_электроснабжение/)).toContainText("OCR: 1 стр.");

    // индикатор полноты: ПСД нет — пункты будут раскрыты частично
    const panel = page.getByTestId("completeness");
    await expect(panel).toContainText(/Нет «Проектно-сметная документация» — пункты .*3\.4\.2.* будут раскрыты частично/);

    // ручная смена категории
    await row(/Бизнес-план/).getByLabel("Категория").selectOption("тэо_bfs");
    await expect(row(/Бизнес-план/)).toContainText("вручную");
    await row(/Бизнес-план/).getByLabel("Категория").selectOption("бизнес_план");

    // предпросмотр с подсветкой фрагмента
    await row(/ТЭО_мукомольный/).getByRole("button", { name: "Просмотр" }).click();
    await expect(page.getByTestId("preview-page")).toHaveText("Стр. 1 из 30");
    await page.getByRole("button", { name: "Вперёд" }).click();
    await page.getByPlaceholder("Найти на странице").fill("320 тонн");
    await page.getByRole("button", { name: "Найти на странице" }).click();
    const img = page.getByRole("dialog").locator("img");
    await expect(img).toHaveAttribute("src", /pages\/2\/image\?q=320/);
    await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(500);
  });
});
