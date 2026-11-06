from multiprocessing import freeze_support

from lectureflow.windows_app import main

if __name__ == "__main__":
    freeze_support()
    main()
