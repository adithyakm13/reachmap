import type { AppProps } from "next/app";

export default function MyApp({ Component, pageProps }: AppProps) {
  return (
    <div className="legacy legacy-wide">
      <Component {...pageProps} />
    </div>
  );
}
