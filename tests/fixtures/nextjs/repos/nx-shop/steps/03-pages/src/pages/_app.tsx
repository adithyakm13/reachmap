import type { AppProps } from "next/app";

export default function MyApp({ Component, pageProps }: AppProps) {
  return (
    <div className="legacy">
      <Component {...pageProps} />
    </div>
  );
}
