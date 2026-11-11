export type Appearance = "light" | "dark" | "system";

export function savedAppearance(): Appearance {
  const saved = localStorage.getItem("undercover-appearance");
  return saved === "light" || saved === "dark" ? saved : "system";
}
