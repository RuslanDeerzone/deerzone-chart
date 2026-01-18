import "./globals.css";

export const metadata = {
  title: "#deerzone chart",
  description: "Russian K-pop chart",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <head>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
      </head>
      <body>{children}</body>
    </html>
  );
}