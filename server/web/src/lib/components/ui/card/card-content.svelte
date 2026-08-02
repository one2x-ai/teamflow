<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import { cn } from "$lib/utils";

  let {
    class: className,
    children,
    ...restProps
  }: HTMLAttributes<HTMLDivElement> & {
    class?: string;
    children: Snippet;
  } = $props();
</script>

<div class={cn("p-6 pt-0", className)} {...restProps}>
  {@render children()}
</div>
