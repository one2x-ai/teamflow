import { tv } from "tailwind-variants";

export const badgeVariants = tv({
  base: "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium transition-colors",
  variants: {
    variant: {
      default: "border-transparent bg-primary text-primary-foreground",
      secondary: "border-transparent bg-secondary text-secondary-foreground",
      outline: "text-foreground",
    },
  },
  defaultVariants: {
    variant: "default",
  },
});
