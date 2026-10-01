import { getRequestConfig } from "next-intl/server";

// Сейчас интерфейс только на русском; KZ/EN — фаза 9 (добавить messages/kk.json, en.json и выбор локали).
export const DEFAULT_LOCALE = "ru";

export default getRequestConfig(async () => {
  const locale = DEFAULT_LOCALE;
  return {
    locale,
    messages: (await import(`../messages/${locale}.json`)).default,
  };
});
