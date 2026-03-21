# PodAngelEX

PodAngelEX is a passion project of mine, specifically made for muting inappropriate words and segments in audio

# How it works

PodAngel takes input files and transcribes them using OpenAI's Whisper model. Depending on your specs, you can choose multiple workers, that run asyncronously, to drastically improve how many files get 'cleaned' in a set amount of time. 

Once there is a list of all words and sentences from the audio file, the program first compares each word with a list of swears. Once it finds all the swears, it 'makes note' of the start and end time of each bad word. Then, so as to not skew the context catching, it 'erases' those swear words from the transcription

Then, the 'new' transcription is passed to Detoxify. Detoxify reads a sentence and assigns multiple values to it, denoting how vulgar it is. If any of those values pass the user-set threshold, the segment is flagged for muting.

Once we have the words and segments to be muted, the start and end times for each are passed to FFMPEG, which cuts and concentates the audio file accordingly, resulting in a much more socially appropriate file

*Please note, I cannot promise absolute accuracy. Rarely, the program may miss one or two swears in my experience, using Whisper's Turbo model

# What can be configured

I made PodAngel with the intent of being highly configurable. You can configure:

    1. Worker amount. There is, functionally, no limit to the amount of workers you want active at one time. They still use VRAM though, so don't just set it to a hundred and let it run if you can't support that.

    2. Toxicity thresholds. Each threshold from Detoxify can be increased or decreased in severity. The lower the number, the stricter the context catching. It's a float from 0.0 to 1.0. 1.0 will let everything through, and 0.0 will let about nothing through

    3. File paths. You can set a file path if you'd like to move PodAngel's 'workspace'. This will move every file/folder that PodAngel relies on to that new path, so if you change it, maybe put it in a fresh folder. 

# Install and How to Run

To get started, just:

    1. run pip install -r utils.txt
    2. run main.py
    3. Complete config init

Then you can put your audio files into the Input folder that should get created, and run the transcription.

Side note: I do realize there are a few warnings that show up, at least on Mac. As far as I know, these warnings don't refrence anything important, and are more annoying than anything else. If there is a real issue that I failed to notice, please let me know! 

## License

CC0 1.0 Universal - Public Domain

## AI Declaration

I used some AI to help debug the code, provide commit messages on Github, and to organize the files for package uploading

