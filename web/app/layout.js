import Script from "next/script";
import "./globals.css";

export const metadata = {
  title: "#deerzone chart",
  description: "Russian K-pop chart",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <head>
        {/* Telegram Mini Apps SDK MUST be in <head> before other scripts */}
        <Script
          src="https://telegram.org/js/telegram-web-app.js"
          strategy="beforeInteractive"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}