import { expect, type Page, test } from "@playwright/test";

import { gotoReady } from "./helpers";

// Сценарии фазы 1 против поднятого стека (make up). Каждый прогон создаёт новую организацию.
const MAILPIT = process.env.E2E_MAILPIT_URL ?? "http://localhost:8025";

function uniq() {
  const n = `${Date.now()}${Math.floor(Math.random() * 1000)}`.slice(-12).padStart(12, "0");
  return { bin: n, email: `e2e-${n}@example.kz` };
}

async function registerOrg(page: Page, org: string) {
  const { bin, email } = uniq();
  await gotoReady(page, "/register");
  await page.getByLabel("Наименование").fill(org);
  await page.getByLabel("БИН").fill(bin);
  await page.getByLabel("ФИО").fill("Тестов Тест Тестович");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Пароль").fill("e2e-secret-1");
  await page.getByRole("button", { name: "Зарегистрировать" }).click();
  // в dev-режиме Next компилирует страницу при первом заходе
  await expect(page).toHaveURL(/\/portfolio$/, { timeout: 20_000 });
  return { email, bin };
}

test("регистрация → эксперт → проект через мастер → проект в портфеле", async ({ page }) => {
  await registerOrg(page, "ТОО «E2E Консалт»");
  await expect(page.getByRole("heading", { name: "Портфель проектов" })).toBeVisible();
  await expect(page.getByText("Проектов пока нет")).toBeVisible();

  // эксперт в реестре — для шага «Команда»
  await page.getByRole("link", { name: "Кабинет" }).click();
  await page.getByRole("link", { name: "Эксперты" }).click();
  await page.getByRole("button", { name: "Добавить эксперта" }).click();
  await page.getByLabel("ФИО").fill("Технологов Т.Т.");
  await page.getByLabel("Специализация").fill("технолог");
  await page.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.getByText("Технологов Т.Т.")).toBeVisible();

  // мастер: паспорт
  await page.getByRole("link", { name: "Портфель" }).click();
  await page.getByRole("link", { name: "Новый проект" }).click();
  await page.getByLabel("Наименование проекта").fill("Мукомольный завод 300 т/сут (e2e)");
  await page.getByRole("textbox", { name: "Заказчик", exact: true }).fill("ТОО «Агро e2e»");
  await page.getByLabel("Отрасль (секция ОКЭД)").selectOption("C");
  await page.getByLabel("Регион").selectOption("Акмолинская область");
  await page.getByLabel("Бюджет проекта").fill("12500000000");
  await page.getByRole("button", { name: "Далее" }).click();

  // независимость: конфликт → предупреждение и обоснование
  await page.getByRole("radiogroup", { name: /Аффилирован/ }).getByLabel("Да").check();
  await expect(page.getByText(/Выявлен потенциальный конфликт интересов/)).toBeVisible();
  await page.getByRole("radiogroup", { name: /Участвовал/ }).getByLabel("Нет").check();
  await page.getByRole("radiogroup", { name: /поставщиком/ }).getByLabel("Нет").check();
  await page.getByLabel("Обоснование независимости").fill("Аффилированность прекращена в 2025 году");
  await page.getByRole("button", { name: "Далее" }).click();

  // команда: эксперт + раздел из шаблона
  await page.getByLabel("Добавить эксперта").selectOption({ label: "Технологов Т.Т. — технолог" });
  await page.getByRole("button", { name: /^3\.4 / }).click();
  await page.getByRole("button", { name: "Создать проект" }).click();

  await expect(page.getByRole("heading", { name: "Мукомольный завод 300 т/сут (e2e)" })).toBeVisible();
  await expect(page.getByText("Черновик", { exact: true }).first()).toBeVisible();

  // проект в портфеле: таблица и карточки
  await page.getByRole("link", { name: "Портфель" }).click();
  const row = page.getByRole("row", { name: /Мукомольный завод 300 т\/сут \(e2e\)/ });
  await expect(row).toBeVisible();
  await expect(row).toContainText("ТОО «Агро e2e»");
  await expect(row).toContainText("12 500 000 000 KZT");
  await page.getByRole("button", { name: "Карточки" }).click();
  await expect(page.getByRole("link", { name: /Мукомольный завод 300 т\/сут \(e2e\)/ })).toBeVisible();

  // фильтр и поиск
  await page.getByPlaceholder("Наименование или Заказчик").fill("нет-такого-проекта");
  await expect(page.getByText("Ничего не найдено")).toBeVisible();
});

test("чужая организация не видит проект", async ({ browser }) => {
  const a = await browser.newPage();
  await registerOrg(a, "ТОО «Альфа e2e»");
  const created = await a.request.post("/api/v1/projects", { data: { name: "Секретный проект", customer_name: "ТОО X" } });
  expect(created.status()).toBe(201);
  const { id } = await created.json();

  const b = await browser.newPage();
  await registerOrg(b, "ТОО «Бета e2e»");
  expect((await b.request.get(`/api/v1/projects/${id}`)).status()).toBe(404);
  await gotoReady(b, `/projects/${id}`);
  await expect(b.getByText("Проект не найден")).toBeVisible();
});

test("восстановление пароля по письму", async ({ page, request }) => {
  const { email } = await registerOrg(page, "ТОО «Сброс e2e»");
  await page.getByRole("button", { name: "Выйти" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.getByRole("link", { name: "Забыли пароль?" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Отправить ссылку" }).click();
  await expect(page.getByText(/письмо со ссылкой уже отправлено/)).toBeVisible();

  let link = "";
  await expect(async () => {
    const search = await request.get(`${MAILPIT}/api/v1/search?query=to:${encodeURIComponent(email)}`);
    const { messages } = await search.json();
    expect(messages.length).toBeGreaterThan(0);
    const msg = await (await request.get(`${MAILPIT}/api/v1/message/${messages[0].ID}`)).json();
    link = msg.Text.match(/https?:\/\/\S+\/reset\/\S+/)[0];
  }).toPass({ timeout: 10_000 });

  await gotoReady(page, new URL(link).pathname);
  await page.getByLabel("Новый пароль").fill("e2e-new-secret-2");
  await page.getByRole("button", { name: "Сменить пароль" }).click();
  await expect(page.getByText("Пароль изменён")).toBeVisible();

  await gotoReady(page, "/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Пароль").fill("e2e-new-secret-2");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/portfolio$/);
});
