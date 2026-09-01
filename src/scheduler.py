import time


def main():
    print("worker started — no jobs scheduled yet (added in Block 2+)")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
