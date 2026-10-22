// The Mantine theme: quiet neutral greys, one accent (an indigo), small radius, a system font stack (no font file, no request), a dark
// palette written for the dark theme (not an inversion of the light one). The colours are the "design tokens" of the workspace: every
// status colour used by a badge is named in `components/badges.tsx` and comes from this palette.

import { createTheme, type MantineColorsTuple } from "@mantine/core";

// ten shades, lightest first (Mantine's convention)
const accent: MantineColorsTuple = ["#f1f2fd", "#e3e5fa", "#c8cbf1", "#a9aee8", "#8d94df", "#7a82d9", "#6a73d4", "#5e6ad2", "#4f5ac0", "#434ca7"];
// the grey of the light theme: very slightly cool
const gray: MantineColorsTuple = ["#fafafb", "#f4f4f6", "#e9e9ed", "#dcdce1", "#bcbcc4", "#8e8e98", "#6c6c77", "#4d4d57", "#2c2c33", "#17171b"];
// the dark theme: index 0 is the text, 7 the page, 6 the surface of a card, 4 the border, 5 a hovered surface
const dark: MantineColorsTuple = ["#ececef", "#c6c6cd", "#9d9da7", "#777782", "#2d2d34", "#25252b", "#18181c", "#0d0d10", "#09090b", "#050506"];
// the blue-grey of "sample"
const slate: MantineColorsTuple = ["#f4f6f9", "#e7ebf1", "#d0d8e4", "#b3c0d3", "#97a8c1", "#7f93b2", "#6c82a5", "#5b7196", "#4d6081", "#40516c"];

export const SYSTEM_SANS =
  'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif, "Apple Color Emoji", "Segoe UI Emoji"';
export const SYSTEM_MONO = 'ui-monospace, "SFMono-Regular", "SF Mono", Menlo, Consolas, "Liberation Mono", monospace';

export const theme = createTheme({
  primaryColor: "accent",
  primaryShade: { light: 7, dark: 4 },
  // a filled button or badge of the accent takes dark or light text by its own contrast, in both themes
  autoContrast: true,
  colors: { accent, gray, dark, slate },
  white: "#ffffff",
  black: "#17171b",
  defaultRadius: "sm",
  fontFamily: SYSTEM_SANS,
  fontFamilyMonospace: SYSTEM_MONO,
  fontSizes: { xs: "0.75rem", sm: "0.8125rem", md: "0.875rem", lg: "1rem", xl: "1.125rem" },
  lineHeights: { xs: "1.4", sm: "1.45", md: "1.5", lg: "1.5", xl: "1.5" },
  headings: {
    fontFamily: SYSTEM_SANS,
    fontWeight: "600",
    sizes: {
      h1: { fontSize: "1.5rem", lineHeight: "1.25" },
      h2: { fontSize: "1.125rem", lineHeight: "1.3" },
      h3: { fontSize: "0.9375rem", lineHeight: "1.35" },
      h4: { fontSize: "0.875rem", lineHeight: "1.4" },
    },
  },
  spacing: { xs: "0.5rem", sm: "0.75rem", md: "1rem", lg: "1.5rem", xl: "2rem" },
  shadows: { xs: "none", sm: "0 1px 2px rgba(0,0,0,0.04)", md: "0 4px 12px rgba(0,0,0,0.08)", lg: "0 8px 24px rgba(0,0,0,0.12)", xl: "0 16px 40px rgba(0,0,0,0.16)" },
  cursorType: "pointer",
  focusRing: "auto",
  respectReducedMotion: true,
  components: {
    Paper: { defaultProps: { withBorder: true, radius: "md" } },
    Card: { defaultProps: { withBorder: true, radius: "md", padding: "md" } },
    Badge: { defaultProps: { radius: "sm", size: "sm", fw: 500 } },
    Table: { defaultProps: { verticalSpacing: 6, horizontalSpacing: "sm", highlightOnHover: true } },
    Button: { defaultProps: { size: "xs" } },
    ActionIcon: { defaultProps: { variant: "subtle", color: "gray", size: "md" } },
    Tabs: { defaultProps: { keepMounted: false } },
    Tooltip: { defaultProps: { openDelay: 250, withArrow: false } },
    Drawer: { defaultProps: { trapFocus: true, returnFocus: true } },
  },
});
