import "./globals.css";
import Script from "next/script";

export const metadata = {
  title: "deerzone chart",
  description: "deerzone chart",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <head>
        <Script
          src="https://telegram.org/js/telegram-web-app.js"
          strategy="beforeInteractive"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
