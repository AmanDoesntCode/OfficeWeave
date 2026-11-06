"""Launch Mail-Merger. Importing this module never opens a window or sends mail."""

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    from mail_merger.desktop import main
    raise SystemExit(main())
