(function() {
        var stored = localStorage.getItem("harnss-theme");
        var isDark = true;
        if (stored === "light") isDark = false;
        else if (stored === "system") isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
        if (!isDark) document.documentElement.classList.remove("dark");
      })();
