<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLButtonAttributes } from "svelte/elements";
  import { cn } from "$lib/utils";
  import { buttonVariants, type ButtonVariant } from "./button-variants";

  let {
    class: className,
    variant = "default",
    size = "default",
    children,
    ...restProps
  }: HTMLButtonAttributes & {
    variant?: ButtonVariant["variant"];
    size?: ButtonVariant["size"];
    class?: string;
    children: Snippet;
  } = $props();
</script>

<button
  class={cn(buttonVariants({ variant, size }), className)}
  {...restProps}
>
  {@render children()}
</button>
