from functions import *
from pathlib import Path

if __name__ == "__main__":
    script_dir = Path(__file__).resolve().parent
    config_path = str(script_dir / "config.json")
    config_dict = read_config(config_path)
    
    if config_dict is None:
        config_menu(script_dir)
        config_dict = read_config(config_path)
    
    if config_dict is None:
        print("Error: Could not load or create config file")
        exit(1)

    main_loop = True

    while main_loop == True:
        print("\n\nWelcome to PodAngel CLI! \nIf you want to reconfigure, type (1)\nYou can run the app with (2)\nType (3) to exit")
        main_input = input("\n Input: ")
        normalized_input = main_input.lower().strip()


        if normalized_input == "1":
            config_menu(script_dir)


        elif normalized_input == "2":
            run_program(config_dict=config_dict, script_dir=script_dir)
            print("Run menu reached")


        elif normalized_input == "3":
            main_loop = False
            quit()