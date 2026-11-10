import { expect, test } from "@playwright/test";

import { gotoReady } from "./helpers";

// Результаты пунктов требуют платного прогона ИИ — ответы API оценки подменяются; проверяется сам
// интерфейс правки: редактор TipTap, сохранение Markdown новой версией, комментарии.
test("ревью: эксперт правит пункт в редакторе и комментирует", async ({ page }) => {
  const n = `${Date.now()}`.slice(-12);
  await gotoReady(page, "/register");
  await page.getByLabel("Наименование").fill("ТОО «E2E Ревью»");
  await page.getByLabel("БИН").fill(n);
  await page.getByLabel("ФИО").fill("Тестов Т.Т.");
  await page.getByLabel("Email").fill(`e2e-review-${n}@example.kz`);
  await page.getByLabel("Пароль").fill("e2e-secret-1");
  await page.getByRole("button", { name: "Зарегистрировать" }).click();
  await expect(page).toHaveURL(/\/portfolio$/, { timeout: 20_000 });
  const created = await page.request.post("/api/v1/projects", {
    data: { name: "Завод (e2e ревью)", customer_name: "ТОО «Агро»" },
  });
  const { id } = await created.json();

  const item = {
    item_id: "3.4.1",
    title: "Обоснованность выбора оборудования и поставщиков",
    section_id: "3.4",
    section_title: "3.4 Оценка проектного оборудования",
    kind: "analysis",
    status: "done",
    version: 1,
    disclosure_status: "partial",
    summary: "Выбор оборудования обоснован частично [S1].",
    error: null,
    review_status: "ai_draft",
    instruction: "",
    started_at: null,
    finished_at: null,
    reused: false,
    author: "ai",
  };
  const payload = {
    item_id: "3.4.1",
    disclosure_status: "partial",
    summary: item.summary,
    analysis_md: "Мощность мельницы 320 т/сут [S1].",
    tables: [],
    findings: [],
    remarks: ["Нет третьего КП"],
    recommendations: [],
    assumptions: [],
    gap: null,
    sources: [{ id: "S1", type: "doc", url: null, title: null, accessed_at: null }],
    confidence: 0.7,
  };
  const detail = {
    ...item,
    payload,
    sources: [
      {
        sid: "S1",
        type: "doc",
        document_id: null,
        filename: "ТЭО.pdf",
        page: 12,
        sheet: null,
        cell_range: null,
        reference_doc_id: null,
        url: null,
        title: "ТЭО.pdf",
        accessed_at: null,
      },
    ],
    warnings: [],
  };
  const run = {
    id: "00000000-0000-0000-0000-000000000001",
    status: "done",
    items_total: 1,
    items_done: 1,
    items_failed: 0,
    cost_usd: "1.00",
    error: null,
    started_at: null,
    finished_at: null,
    web_search: true,
  };
  const base = `**/api/v1/projects/${id}`;
  await page.route(`${base}/assessment`, (r) => r.fulfill({ json: { run, items: [item] } }));
  await page.route(`${base}/assessment/items/3.4.1`, (r) => r.fulfill({ json: detail }));
  await page.route(`${base}/assessment/items/3.4.1/versions`, (r) => r.fulfill({ json: [] }));
  const comments: unknown[] = [];
  await page.route(`${base}/comments?ref=3.4.1`, (r) => r.fulfill({ json: comments }));
  let saved: Record<string, unknown> | null = null;
  await page.route(`${base}/assessment/items/3.4.1/edit`, async (r) => {
    saved = r.request().postDataJSON();
    await r.fulfill({ json: { ...detail, version: 2, author: "user", review_status: "in_review" } });
  });
  let comment: Record<string, unknown> | null = null;
  await page.route(`${base}/comments`, async (r) => {
    comment = r.request().postDataJSON();
    comments.push({ id: "c1", ref: "3.4.1", author: "Тестов Т.Т.", text: comment!.text, created_at: new Date().toISOString(), resolved_at: null, resolved_by: null });
    await r.fulfill({ status: 201, json: comments[0] });
  });

  await gotoReady(page, `/projects/${id}?tab=assessment`);
  await page.getByRole("button", { name: /3\.4\.1/ }).click();
  const panel = page.getByTestId("item-panel");
  await expect(panel).toContainText("Мощность мельницы 320 т/сут");

  await panel.getByRole("button", { name: "Редактировать" }).click();
  const editor = panel.getByTestId("item-editor");
  const md = editor.getByLabel("Аргументация");
  await expect(md).toContainText("Мощность мельницы 320 т/сут [S1]");
  await md.click();
  await page.keyboard.press("End");
  await page.keyboard.type(" Третье КП запрошено у Заказчика.");
  await editor.getByLabel("Краткий вывод").fill("Выбор оборудования обоснован [S1].");
  await editor.getByLabel("Что исправлено").fill("Уточнён вывод");
  await editor.getByRole("button", { name: "Сохранить новой версией" }).click();

  await expect.poll(() => saved).not.toBeNull();
  expect(saved!.summary).toBe("Выбор оборудования обоснован [S1].");
  expect(String(saved!.analysis_md)).toContain("Мощность мельницы 320 т/сут [S1]");
  expect(String(saved!.analysis_md)).toContain("Третье КП запрошено у Заказчика.");
  expect(saved!.remarks).toEqual(["Нет третьего КП"]);
  expect(saved!.note).toBe("Уточнён вывод");
  await expect(panel.getByTestId("item-editor")).toHaveCount(0);

  await panel.getByLabel("Комментарий для экспертов").fill("Проверить цену мельницы");
  await panel.getByRole("button", { name: "Добавить комментарий" }).click();
  await expect.poll(() => comment).toEqual({ ref: "3.4.1", text: "Проверить цену мельницы" });
});
