from utils.ecoco_sequence_loader import *
from model.model import E2VIDRecurrent
from utils.train_utils import PreProcessOptions, RescalerOptions
from utils.inference_utils import EventPreprocessor, IntensityRescaler
from utils.train_utils import plot_training_data, pad_all, loss_fn, training_loop
from utils.loading_utils import get_device
from utils.ecoco_dataset import ECOCO_Train_Dataset, ECOCO_Validation_Dataset
import lpips

if __name__ == "__main__":
    # ======================================================================================================================================================
    # Model definition
    config = {'recurrent_block_type': 'convlstm', 'num_bins': 5, 'skip_type': 'sum', 'num_encoders': 3,
              'base_num_channels': 32, 'num_residual_blocks': 2, 'norm': 'BN', 'use_upsample_conv': True}
    model = E2VIDRecurrent(config=config).cuda()

    # Event preprocessor
    options = PreProcessOptions()
    preprocessor = EventPreprocessor(options)
    options = RescalerOptions()
    rescaler = IntensityRescaler(options)

    # ignore the code above, they are just used for taking out the event tensor and model
    device = get_device(True)
    # DATA_DIR = '/home/richard/Q3/Deep_Learning/ruben-mr.github.io/data'
    seq_length = 3
    start_idx = 8
    data_path = DATA_DIR
    sequence_index = 42

    train_dataset = ECOCO_Train_Dataset(sequence_length=seq_length, start_index=start_idx, path=data_path)
    val_dataset = ECOCO_Validation_Dataset(sequence_length=seq_length, start_index=start_idx, path=data_path)

    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=2, shuffle=True)
    val_loader = torch.utils.data.DataLoader(train_dataset, batch_size=2, shuffle=True)

    if torch.cuda.is_available():
        reconstruction_loss_fn = lpips.LPIPS(net='vgg').cuda()
    else:
        reconstruction_loss_fn = lpips.LPIPS(net='vgg')

    train_losses, val_losses = training_loop(model, loss_fn, train_loader, val_loader, reconstruction_loss_fn, epoch=2)
    print(train_losses)
    print(val_losses)
    plot_training_data(train_losses, val_losses)
