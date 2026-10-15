import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  ...nextVitals,
  ...nextTs,
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
  // Data-loading effects intentionally set loading state before fetching.
  { rules: { "react-hooks/set-state-in-effect": "off" } },
];

export default config;
