export const metadata = {
  title: "#deerzone chart",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <head>
        <script
          src="https://telegram.org/js/telegram-web-app.js"
          defer
        ></script>
      </head>
      <body>{children}</body>
    </html>
  );
}