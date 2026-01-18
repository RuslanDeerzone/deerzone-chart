import "./globals.css";
import Script from "next/script";

export const metadata = {
  title: "#deerzone chart",
  description: "Russian K-pop chart",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <head>
        {/* ВАЖНО: telegram-web-app.js должен быть в <head> до остальных скриптов */}
        <Script
          src="https://telegram.org/js/telegram-web-app.js"
          strategy="beforeInteractive"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
