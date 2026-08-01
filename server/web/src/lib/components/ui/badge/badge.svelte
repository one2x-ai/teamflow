<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import type { VariantProps } from "tailwind-variants";
  import { cn } from "$lib/utils";
  import { badgeVariants } from "./badge-variants";

  let {
    class: className,
    variant = "secondary",
    children,
    ...restProps
  }: HTMLAttributes<HTMLSpanElement> & {
    variant?: VariantProps<typeof badgeVariants>["variant"];
    children: Snippet;
  } = $props();
</script>

<span class={cn(badgeVariants({ variant }), className)} {...restProps}>
  {@render children()}
</span>
