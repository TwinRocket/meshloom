import { Toaster as Sonner, toast } from 'sonner';

type ToasterProps = React.ComponentProps<typeof Sonner>;

/**
 * Sonner injects its own stylesheet at runtime, unlayered, and
 * `[data-sonner-toast][data-styled=true]` sets the toast's background, border,
 * colour and shadow. An unlayered rule beats every Tailwind 4 utility (they live
 * in `@layer utilities`), so the theme classes on the toast and the error toast
 * carry `!`, which puts them above any normal declaration. The description and
 * button classes are left as they were: Sonner's equally specific rules, injected
 * after the app stylesheet, already won over them under Tailwind 3.
 */
const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme="dark"
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            'group toast group-[.toaster]:bg-card! group-[.toaster]:text-foreground! group-[.toaster]:border-border! group-[.toaster]:shadow-lg!',
          description: 'group-[.toast]:text-muted-foreground',
          actionButton: 'group-[.toast]:bg-primary group-[.toast]:text-primary-foreground',
          cancelButton: 'group-[.toast]:bg-muted group-[.toast]:text-muted-foreground',
          // Muted error style - dark red-tinted background with readable text
          error:
            'group-[.toaster]:bg-toast-error! group-[.toaster]:text-toast-error-foreground! group-[.toaster]:border-toast-error-border! **:data-description:text-toast-error-foreground',
        },
      }}
      closeButton
      {...props}
    />
  );
};

export { Toaster, toast };
