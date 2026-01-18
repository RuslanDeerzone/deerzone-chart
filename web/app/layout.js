import "./globals.css";

export const metadata = {
  title: "#deerzone chart",
  description: "Russian K-pop chart",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}